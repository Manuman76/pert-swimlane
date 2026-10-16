from datetime import datetime
from .db import db
from .workdays import add_workdays


class Project(db.Model):
    __tablename__ = "projects"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.Text, nullable=False, unique=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    lanes = db.relationship("Lane", backref="project", lazy=True, cascade="all, delete-orphan")
    tasks = db.relationship("Task", backref="project", lazy=True, cascade="all, delete-orphan")


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
    lane_id = db.Column(db.Integer, db.ForeignKey("lanes.id"), nullable=False)
    name = db.Column(db.Text, nullable=False)
    start_date = db.Column(db.Date, nullable=False)
    duration_days = db.Column(db.Integer, nullable=False)
    responsible = db.Column(db.Text, nullable=True)
    predecessors = db.Column(db.Text, nullable=True)
    started = db.Column(db.Boolean, nullable=False, default=False)
    done = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (db.UniqueConstraint("project_id", "code", name="uq_task_code"),)

    @property
    def end_date(self):
        """Exclusive finish: the first day a successor may start.

        `duration_days` is counted in **working days** (5-day week, Saturday
        and Sunday skipped), so the end date is always a Monday-Friday date.
        A 3-day task starting Thursday 1 Jun ends Tue 6 Jun: it is worked on
        Thu 1, Fri 2 and Mon 5, and the weekend is not task time.
        """
        if not self.start_date:
            return None
        return add_workdays(self.start_date, self.duration_days or 0)

    @property
    def status(self):
        """One of 'not_started', 'started', 'done' (done wins)."""
        if self.done:
            return "done"
        if self.started:
            return "started"
        return "not_started"

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
