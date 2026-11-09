"""Shared parsing and sequence formatting helpers."""

from datetime import datetime, date
from ..db import db
from ..models import (
    Lane,
    Task,
    TeamMember,
    MemberAbsence,
    Requirement,
    RaidItem,
    MOSCOW_VALUES,
    DEFAULT_MOSCOW,
    RAID_KINDS,
    RAID_STATUS_OPEN,
    RAID_STATUS_VALUES,
    RAID_LEVEL_VALUES,
    RAID_CODE_PREFIX,
)
from ..workdays import next_workday


def fmt_allocation(task):
    """How much of its assignee a task takes, in words (the grid tooltip).

    ``None`` is the pre-capacity meaning — the whole person — so it reads the same
    as a stored 100. Zero is worth spelling out, because a task that asks nothing
    of its owner is a data entry mistake people want to see named.
    """
    percent = getattr(task, "allocation_percent", None)
    if percent is None or percent >= 100:
        return "Whole of this person's time"
    if percent <= 0:
        return "Nothing: this task asks no time of its owner"
    return f"{percent}% of this person's time"


def fmt_date(d):
    """Format a date as '01 Jan 2026'."""
    return d.strftime("%d %b %Y") if d else ""


def parse_date(s, calendar=None):
    """Parse an entered date, rolled forward onto a working day.

    A start date typed on a non-working day or holiday rolls forward to the
    next working day according to the project calendar.
    """
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%d %b %Y", "%d %B %Y"):
        try:
            return next_workday(datetime.strptime(s.strip(), fmt).date(), calendar=calendar)
        except ValueError:
            continue
    return None


def parse_flexible_date(s, default_year=None):
    """Parse a date typed by a person, with no working-day roll.

    Accepts ISO plus the formats the settings date inputs and hand-typed entries
    produce, including a bare "Jul 6" which borrows ``default_year`` (today's by
    default). Shared by holidays and absences; task dates use :func:`parse_date`,
    which rolls forward onto the calendar instead.
    """
    if not s or not s.strip():
        return None
    text = s.strip()
    year = default_year or date.today().year
    for fmt in ("%Y-%m-%d", "%d %b %Y", "%d %B %Y", "%b %d %Y", "%B %d %Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            pass
    for fmt in ("%b %d", "%d %b", "%B %d", "%d %B"):
        try:
            return datetime.strptime(text, fmt).date().replace(year=year)
        except ValueError:
            pass
    return None


def normalize_rich_text(value):
    """Store Quill HTML, treating blank editor content as NULL."""
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    # Quill empty document variants
    stripped = (
        text.replace("&nbsp;", " ")
        .replace("<p><br></p>", "")
        .replace("<p></p>", "")
        .replace("<br>", "")
        .replace("<br/>", "")
        .strip()
    )
    if not stripped or not stripped.replace("<p>", "").replace("</p>", "").strip():
        return None
    return text


def parse_moscow(raw):
    value = (raw or "").strip().lower()
    return value if value in MOSCOW_VALUES else DEFAULT_MOSCOW


def next_task_code(project_id):
    max_code = db.session.query(db.func.max(Task.code)).filter_by(project_id=project_id).scalar()
    if max_code:
        next_num = int(max_code) + 1
    else:
        next_num = 1
    return f"{next_num:03d}"


def next_requirement_code(project_id):
    count = Requirement.query.filter_by(project_id=project_id).count() + 1
    while Requirement.query.filter_by(project_id=project_id, code=f"BR-{count:03d}").first():
        count += 1
    return f"BR-{count:03d}"


def next_raid_code(project_id, kind):
    prefix = f"{RAID_CODE_PREFIX[kind]}-"
    max_num = 0
    for item in RaidItem.query.filter_by(project_id=project_id, kind=kind):
        if item.code.startswith(prefix):
            suffix = item.code[len(prefix):]
            if suffix.isdigit():
                max_num = max(max_num, int(suffix))
    return f"{prefix}{max_num + 1:03d}"


def parse_percent_field(raw, label="Progress", minimum=0, maximum=100):
    """Read a whole-number percentage out of a form field: ``(value, error)``.

    Blank means "not recorded" (``None``), which for progress is *auto* and for
    task allocation is the whole of the person. Only decimal digits are accepted,
    so ``60%``, ``1.5`` and a pasted superscript are refused with a message
    instead of reaching :func:`int` and raising.
    """
    text = (raw or "").strip()
    if not text:
        return None, None
    message = f"{label} must be a whole number from {minimum} to {maximum}."
    if not text.isdecimal():
        return None, message
    value = int(text)
    if not minimum <= value <= maximum:
        return None, message
    return value, None


def parse_raid_kind(raw):
    kind = (raw or "").strip().lower()
    return kind if kind in RAID_KINDS else None


def parse_raid_status(raw, default=RAID_STATUS_OPEN):
    value = (raw or default).strip().lower()
    return value if value in RAID_STATUS_VALUES else default


def parse_raid_level(raw):
    if raw is None or raw == "":
        return None
    value = raw.strip().lower()
    return value if value in RAID_LEVEL_VALUES else None


def lanes_in_order(project_id):
    """The project's lanes in swimlane order - what the grid's Lane list shows."""
    return Lane.query.filter_by(project_id=project_id).order_by(Lane.sort_order).all()


def members_in_order(project_id):
    return TeamMember.query.filter_by(project_id=project_id).order_by(
        db.func.lower(TeamMember.name), TeamMember.id
    ).all()


def absences_in_order(project_id):
    """Every recorded absence by member name, then by first day away."""
    return (
        MemberAbsence.query
        .join(TeamMember, MemberAbsence.member_id == TeamMember.id)
        .filter(MemberAbsence.project_id == project_id)
        .order_by(
            db.func.lower(TeamMember.name),
            TeamMember.id,
            MemberAbsence.start_date,
            MemberAbsence.id,
        )
        .all()
    )


def member_roles_in_use(project_id):
    """Roles already typed for this project, for the settings datalist.

    Deduplicated in Python rather than SQL, because the comparison ignores spacing:
    a role stored as ``"Lead "`` is the same role as ``"Lead"``. Case still matters -
    ``"Backend"`` and ``"backend"`` are offered as the two different roles they are.
    """
    rows = (
        db.session.query(TeamMember.role)
        .filter(TeamMember.project_id == project_id, TeamMember.role.isnot(None))
        .all()
    )
    return sorted({row[0].strip() for row in rows if row[0] and row[0].strip()},
                  key=lambda role: (role.lower(), role))


def requirements_in_order(project_id):
    return Requirement.query.filter_by(project_id=project_id).order_by(
        Requirement.code
    ).all()
