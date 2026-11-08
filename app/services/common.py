"""Shared parsing and sequence formatting helpers."""

from datetime import datetime
from ..db import db
from ..models import (
    Lane,
    Task,
    TeamMember,
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


def requirements_in_order(project_id):
    return Requirement.query.filter_by(project_id=project_id).order_by(
        Requirement.code
    ).all()
