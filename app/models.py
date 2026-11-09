from datetime import datetime, timedelta
from .db import db
from .workdays import add_workdays


requirement_tasks = db.Table(
    "requirement_tasks",
    db.Column("requirement_id", db.Integer, db.ForeignKey("requirements.id"), primary_key=True),
    db.Column("task_id", db.Integer, db.ForeignKey("tasks.id"), primary_key=True),
)


def _fmt_day(d):
    """Day number without a leading zero (``6``, not ``06``)."""
    return str(d.day)


def _fmt_short_day(d):
    """``6 Jul``."""
    return f"{d.day} {d.strftime('%b')}"


def _fmt_long_day(d):
    """``6 Jul 2026``."""
    return f"{d.day} {d.strftime('%b %Y')}"


class Project(db.Model):
    __tablename__ = "projects"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.Text, nullable=False, unique=True)
    description = db.Column(db.Text, nullable=True)
    baseline_set_at = db.Column(db.DateTime, nullable=True)
    workdays = db.Column(db.Text, nullable=False, default="0,1,2,3,4")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    lanes = db.relationship("Lane", backref="project", lazy=True, cascade="all, delete-orphan")
    tasks = db.relationship("Task", backref="project", lazy=True, cascade="all, delete-orphan")
    members = db.relationship("TeamMember", backref="project", lazy=True, cascade="all, delete-orphan")
    absences = db.relationship("MemberAbsence", backref="project", lazy=True, cascade="all, delete-orphan")
    requirements = db.relationship("Requirement", backref="project", lazy=True, cascade="all, delete-orphan")
    raid_items = db.relationship("RaidItem", backref="project", lazy=True, cascade="all, delete-orphan")
    holidays = db.relationship("Holiday", backref="project", lazy=True, cascade="all, delete-orphan", order_by="Holiday.date")

    @property
    def workdays_list(self):
        if not self.workdays:
            return [0, 1, 2, 3, 4]
        try:
            nums = [int(x.strip()) for x in self.workdays.split(",") if x.strip() and x.strip().isdigit()]
            return nums if nums else [0, 1, 2, 3, 4]
        except Exception:
            return [0, 1, 2, 3, 4]

    @property
    def workdays_set(self):
        return set(self.workdays_list)

    @property
    def calendar(self):
        from .workdays import WorkdayCalendar
        holiday_dates = {h.date for h in self.holidays} if self.holidays else set()
        return WorkdayCalendar(workdays=self.workdays_set, holidays=holiday_dates)


class Holiday(db.Model):
    __tablename__ = "holidays"
    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey("projects.id"), nullable=False)
    date = db.Column(db.Date, nullable=False)
    name = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    __table_args__ = (db.UniqueConstraint("project_id", "date", name="uq_project_holiday_date"),)


class TeamMember(db.Model):
    __tablename__ = "team_members"
    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey("projects.id"), nullable=False)
    name = db.Column(db.Text, nullable=False)
    # Capacity is availability, not seniority: 100 means "one person-day per working
    # day of the project calendar", 60 means three-fifths of that (part-time, shared
    # with another project, split role). app/capacity.py turns it into weekly supply.
    role = db.Column(db.Text, nullable=True)
    capacity_percent = db.Column(db.Integer, nullable=False, default=100)
    cost_per_day = db.Column(db.Numeric(8, 2, asdecimal=False), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    __table_args__ = (db.UniqueConstraint("project_id", "name", name="uq_member_name"),)

    absences = db.relationship(
        "MemberAbsence",
        back_populates="member",
        lazy=True,
        cascade="all, delete-orphan",
        order_by="MemberAbsence.start_date",
    )

    @property
    def capacity_fraction(self):
        """Availability as a fraction of one full-time person (``None`` → 1.0)."""
        if self.capacity_percent is None:
            return 1.0
        return max(0.0, min(float(self.capacity_percent), 400.0)) / 100.0


class MemberAbsence(db.Model):
    """Days a named resource is not delivering on this project.

    ``end_date`` is **exclusive**, like :attr:`Task.end_date`: a single day off is
    ``start_date == 2026-07-06, end_date == 2026-07-07``. Absences never change dates —
    they only lower the supply side of the capacity calculation.
    """

    __tablename__ = "member_absences"
    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey("projects.id"), nullable=False)
    member_id = db.Column(
        db.Integer, db.ForeignKey("team_members.id", ondelete="cascade"), nullable=False
    )
    start_date = db.Column(db.Date, nullable=False)
    end_date = db.Column(db.Date, nullable=False)
    reason = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    member = db.relationship("TeamMember", back_populates="absences")

    __table_args__ = (
        db.UniqueConstraint("member_id", "start_date", "end_date", name="uq_absence_range"),
    )

    @property
    def is_multi_day(self):
        return self.end_date - self.start_date > timedelta(days=1)

    @property
    def label(self):
        """Human range for lists: ``6 Jul 2026`` for one day, ``6 - 17 Jul 2026`` for a span."""
        last = self.end_date - timedelta(days=1)
        if last <= self.start_date:
            return _fmt_long_day(self.start_date)
        if (last.month, last.year) == (self.start_date.month, self.start_date.year):
            return f"{_fmt_day(self.start_date)} - {_fmt_long_day(last)}"
        if last.year == self.start_date.year:
            return f"{_fmt_short_day(self.start_date)} - {_fmt_long_day(last)}"
        return f"{_fmt_long_day(self.start_date)} - {_fmt_long_day(last)}"


# MoSCoW priority for business requirements (stored value -> label).
MOSCOW_CHOICES = (
    ("must", "Must have"),
    ("should", "Should have"),
    ("could", "Could have"),
    ("wont", "Won't have"),
)
MOSCOW_VALUES = {value for value, _label in MOSCOW_CHOICES}
DEFAULT_MOSCOW = "must"

PRIORITY_CHOICES = (
    ("low", "Low"),
    ("medium", "Medium"),
    ("high", "High"),
)
PRIORITY_VALUES = {value for value, _label in PRIORITY_CHOICES}
DEFAULT_PRIORITY = "medium"

TASK_KIND_TASK = "task"
TASK_KIND_MILESTONE = "milestone"
TASK_KINDS = (TASK_KIND_TASK, TASK_KIND_MILESTONE)


class Requirement(db.Model):
    __tablename__ = "requirements"
    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey("projects.id"), nullable=False)
    code = db.Column(db.Text, nullable=False)
    title = db.Column(db.Text, nullable=False)
    description = db.Column(db.Text, nullable=True)
    moscow = db.Column(db.Text, nullable=False, default=DEFAULT_MOSCOW)
    status = db.Column(db.Text, nullable=False, default="open")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    tasks = db.relationship("Task", secondary=requirement_tasks, back_populates="requirements")
    __table_args__ = (db.UniqueConstraint("project_id", "code", name="uq_requirement_code"),)

    @property
    def traceability(self):
        from .requirement_trace import traceability_for
        return traceability_for(self)


RAID_KIND_RISK = "risk"
RAID_KIND_ASSUMPTION = "assumption"
RAID_KIND_ISSUE = "issue"
RAID_KIND_DECISION = "decision"
RAID_KINDS = (RAID_KIND_RISK, RAID_KIND_ASSUMPTION, RAID_KIND_ISSUE, RAID_KIND_DECISION)
RAID_KIND_LABELS = {
    RAID_KIND_RISK: "Risks",
    RAID_KIND_ASSUMPTION: "Assumptions",
    RAID_KIND_ISSUE: "Issues",
    RAID_KIND_DECISION: "Decisions",
}
RAID_CODE_PREFIX = {
    RAID_KIND_RISK: "R",
    RAID_KIND_ASSUMPTION: "A",
    RAID_KIND_ISSUE: "I",
    RAID_KIND_DECISION: "D",
}

RAID_STATUS_OPEN = "open"
RAID_STATUS_CLOSED = "closed"
RAID_STATUS_CHOICES = (
    (RAID_STATUS_OPEN, "Open"),
    (RAID_STATUS_CLOSED, "Closed"),
)
RAID_STATUS_VALUES = {value for value, _ in RAID_STATUS_CHOICES}

RAID_LEVEL_LOW = "low"
RAID_LEVEL_MEDIUM = "medium"
RAID_LEVEL_HIGH = "high"
RAID_LEVEL_CHOICES = (
    (RAID_LEVEL_LOW, "Low"),
    (RAID_LEVEL_MEDIUM, "Medium"),
    (RAID_LEVEL_HIGH, "High"),
)
RAID_LEVEL_VALUES = {value for value, _ in RAID_LEVEL_CHOICES}


class RaidItem(db.Model):
    __tablename__ = "raid_items"
    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey("projects.id"), nullable=False)
    kind = db.Column(db.Text, nullable=False)
    code = db.Column(db.Text, nullable=False)
    title = db.Column(db.Text, nullable=False)
    description = db.Column(db.Text, nullable=True)
    owner = db.Column(db.Text, nullable=True)
    status = db.Column(db.Text, nullable=False, default=RAID_STATUS_OPEN)
    probability = db.Column(db.Text, nullable=True)
    impact = db.Column(db.Text, nullable=True)
    mitigation = db.Column(db.Text, nullable=True)
    severity = db.Column(db.Text, nullable=True)
    due_date = db.Column(db.Date, nullable=True)
    decision_date = db.Column(db.Date, nullable=True)
    decision_text = db.Column(db.Text, nullable=True)
    rationale = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (db.UniqueConstraint("project_id", "code", name="uq_raid_code"),)


class Lane(db.Model):
    __tablename__ = "lanes"
    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey("projects.id"), nullable=False)
    name = db.Column(db.Text, nullable=False)
    sort_order = db.Column(db.Integer, nullable=False)

    __table_args__ = (db.UniqueConstraint("project_id", "sort_order", name="uq_lane_order"),)

    tasks = db.relationship("Task", backref="lane", lazy=True)


class Task(db.Model):
    __tablename__ = "tasks"
    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey("projects.id"), nullable=False)
    code = db.Column(db.Text, nullable=False)
    kind = db.Column(db.Text, nullable=False, default=TASK_KIND_TASK)
    lane_id = db.Column(db.Integer, db.ForeignKey("lanes.id"), nullable=False)
    name = db.Column(db.Text, nullable=False)
    start_date = db.Column(db.Date, nullable=False)
    duration_days = db.Column(db.Integer, nullable=False)
    responsible = db.Column(db.Text, nullable=True)
    predecessors = db.Column(db.Text, nullable=True)
    description = db.Column(db.Text, nullable=True)
    notes = db.Column(db.Text, nullable=True)
    links = db.Column(db.Text, nullable=True)
    priority = db.Column(db.Text, nullable=False, default=DEFAULT_PRIORITY)
    # Share of the assignee's working day this task consumes while it runs.
    # NULL means 100%; effort = duration_days x allocation_percent x 1%.
    allocation_percent = db.Column(db.Integer, nullable=True)
    progress_percent = db.Column(db.Integer, nullable=True)
    started = db.Column(db.Boolean, nullable=False, default=False)
    done = db.Column(db.Boolean, nullable=False, default=False)
    baseline_start_date = db.Column(db.Date, nullable=True)
    baseline_end_date = db.Column(db.Date, nullable=True)
    baseline_duration_days = db.Column(db.Integer, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (db.UniqueConstraint("project_id", "code", name="uq_task_code"),)

    requirements = db.relationship("Requirement", secondary=requirement_tasks, back_populates="tasks")

    @property
    def is_milestone(self):
        return self.kind == TASK_KIND_MILESTONE

    @property
    def has_baseline(self):
        return self.baseline_start_date is not None or self.baseline_end_date is not None

    @property
    def calendar(self):
        return getattr(self.project, "calendar", None) if getattr(self, "project", None) else None

    @property
    def variance_days(self):
        """Signed working-day difference between current date and baseline date.
        Positive: delayed (e.g. +2d). Negative: ahead (e.g. -2d). Zero: on track.
        """
        from .workdays import diff_workdays
        cal = self.calendar
        if self.is_milestone:
            if not self.baseline_start_date or not self.start_date:
                return 0
            return diff_workdays(self.baseline_start_date, self.start_date, calendar=cal)
        else:
            if not self.baseline_end_date or not self.end_date:
                return 0
            return diff_workdays(self.baseline_end_date, self.end_date, calendar=cal)

    @property
    def end_date(self):
        """Exclusive finish for tasks; for milestones, the target date itself."""
        if not self.start_date:
            return None
        if self.is_milestone:
            return self.start_date
        return add_workdays(self.start_date, self.duration_days or 0, calendar=self.calendar)

    @property
    def status(self):
        """One of 'not_started', 'started', 'done' (done wins)."""
        if self.done:
            return "done"
        if self.started:
            return "started"
        return "not_started"

    @property
    def display_progress(self):
        """Progress bar value 0–100 (explicit percent or inferred from status)."""
        if self.progress_percent is not None:
            return max(0, min(100, int(self.progress_percent)))
        if self.done:
            return 100
        if self.started:
            return 50
        return 0

    def predecessor_codes(self):
        if not self.predecessors:
            return []
        codes = [c.strip() for c in self.predecessors.split(",") if c.strip()]
        seen = set()
        result = []
        for c in codes:
            if c not in seen:
                seen.add(c)
                result.append(c)
        return result
