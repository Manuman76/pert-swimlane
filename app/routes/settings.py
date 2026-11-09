"""Settings routes: lanes, team members, availability and the project calendar."""

import re
from flask import render_template, request, redirect, url_for, make_response

from ..db import db
from ..models import (
    Project,
    Lane,
    TeamMember,
    Task,
    Holiday,
    CAPACITY_PERCENT_MIN,
    CAPACITY_PERCENT_MAX,
    CAPACITY_PERCENT_DEFAULT,
)
from ..services.common import (
    lanes_in_order,
    member_roles_in_use,
    members_in_order,
    parse_flexible_date,
)
from ..services.scheduler import recalculate_project
from ..workdays import WEEKDAY_NAMES
from .context import bp

# Numeric(8,2) column: nine significant digits, two of them decimals.
COST_PER_DAY_MAX = 999999.99
# Free text, shown on one line in the member list and the capacity row header.
ROLE_MAX_LENGTH = 60


def swap_lane_orders(a, b):
    """Swap the sort_order values of two lanes in the same project.

    A direct swap — whether via tuple assignment or a single CASE UPDATE —
    violates the UNIQUE(project_id, sort_order) constraint: SQLite applies
    row updates one at a time and checks the constraint against the table as
    it stands, so the first row ends up with a value the other row still
    holds. Route `a` through a guaranteed-free value, flushing after each
    step so one UPDATE runs per step, then place `b`, then bring `a` home.
    """
    va, vb = a.sort_order, b.sort_order
    if va == vb:
        return
    free = db.session.query(db.func.max(Lane.sort_order)).filter_by(project_id=a.project_id).scalar() + 1
    a.sort_order = free
    db.session.flush()
    b.sort_order = va
    db.session.flush()
    a.sort_order = vb
    db.session.commit()


# ---------------------------------------------------------------------------
# Lane Operations
# ---------------------------------------------------------------------------

def add_lane(project, name):
    name = (name or "").strip()
    if not name:
        return "Lane name is required", 400
    max_order = db.session.query(db.func.max(Lane.sort_order)).filter_by(project_id=project.id).scalar() or 0
    lane = Lane(project_id=project.id, name=name, sort_order=max_order + 1)
    db.session.add(lane)
    db.session.commit()
    return lane, None


def rename_lane(project, lane_id, name):
    lane = Lane.query.filter_by(id=lane_id, project_id=project.id).first_or_404()
    name = (name or "").strip()
    if name:
        lane.name = name
        db.session.commit()
    return lane, None


def move_lane_order(project, lane_id, direction):
    lane = Lane.query.filter_by(id=lane_id, project_id=project.id).first_or_404()
    if direction == "up":
        target = Lane.query.filter(
            Lane.project_id == project.id, Lane.sort_order < lane.sort_order
        ).order_by(Lane.sort_order.desc()).first()
    else:
        target = Lane.query.filter(
            Lane.project_id == project.id, Lane.sort_order > lane.sort_order
        ).order_by(Lane.sort_order).first()
    if target:
        swap_lane_orders(lane, target)
    return lane, None


def delete_lane(project, lane_id):
    lane = Lane.query.filter_by(id=lane_id, project_id=project.id).first_or_404()
    if lane.tasks:
        remaining = Lane.query.filter(Lane.project_id == project.id, Lane.id != lane.id).order_by(Lane.sort_order).first()
        if not remaining:
            return None, ("Cannot delete the last lane with tasks. Create another lane first.", 400)
        for t in lane.tasks:
            t.lane_id = remaining.id
    db.session.delete(lane)
    db.session.commit()
    lanes = Lane.query.filter_by(project_id=project.id).order_by(Lane.sort_order).all()
    for i, l in enumerate(lanes, 1):
        l.sort_order = i
    db.session.commit()
    return None, None


@bp.route("/projects/<int:id>/lanes", methods=["POST"])
def manage_lanes(id):
    """Manage the project's configured swimlanes from Project settings."""
    proj = Project.query.get_or_404(id)
    action = request.values.get("action")
    error = None

    if action == "add":
        _, error = add_lane(proj, request.form.get("name"))
    elif action == "rename":
        _, error = rename_lane(proj, request.form.get("lane_id", type=int), request.form.get("name"))
    elif action == "move_up":
        _, error = move_lane_order(proj, request.form.get("lane_id", type=int), "up")
    elif action == "move_down":
        _, error = move_lane_order(proj, request.form.get("lane_id", type=int), "down")
    elif action == "delete":
        _, error = delete_lane(proj, request.form.get("lane_id", type=int))

    if error:
        return error

    if request.form.get("partial") == "settings":
        return render_template(
            "partials/settings_lanes.html",
            project=proj,
            lanes=lanes_in_order(proj.id),
        )

    fragment = "#settings" if request.form.get("tab") == "settings" else ""
    return redirect(url_for("main.project", id=proj.id) + fragment)


# ---------------------------------------------------------------------------
# Member Operations
# ---------------------------------------------------------------------------

def add_team_member(project, name, capacity_percent=None):
    name = (name or "").strip()
    if not name:
        return None, ("Team member name is required", 400)
    capacity, error = parse_capacity_percent(capacity_percent)
    if error:
        return None, error
    if TeamMember.query.filter(
        TeamMember.project_id == project.id,
        db.func.lower(TeamMember.name) == name.lower(),
    ).first():
        return None, ("That team member already exists", 400)
    member = TeamMember(project_id=project.id, name=name, capacity_percent=capacity)
    db.session.add(member)
    db.session.commit()
    return member, None


def rename_team_member(project, member_id, name):
    member = TeamMember.query.filter_by(id=member_id, project_id=project.id).first_or_404()
    name = (name or "").strip()
    if not name:
        return None, ("Team member name is required", 400)
    duplicate = TeamMember.query.filter(
        TeamMember.project_id == project.id,
        TeamMember.id != member.id,
        db.func.lower(TeamMember.name) == name.lower(),
    ).first()
    if duplicate:
        return None, ("That team member already exists", 400)
    old_name = member.name
    member.name = name
    for task in Task.query.filter_by(project_id=project.id, responsible=old_name):
        task.responsible = name
    db.session.commit()
    return member, None


def parse_capacity_percent(raw):
    """(percent, error) for the capacity field: blank means full time.

    A non-number, a value outside 0-400, or a decimal is an error rather than
    something to round away: the number feeds every week in the plan.
    """
    if raw is None or not str(raw).strip():
        return CAPACITY_PERCENT_DEFAULT, None
    text = str(raw).strip()
    if not re.fullmatch(r"[+-]?\d+", text):
        return None, (f"Capacity must be a whole number ({CAPACITY_PERCENT_MIN}-{CAPACITY_PERCENT_MAX})", 400)
    value = int(text)
    if not CAPACITY_PERCENT_MIN <= value <= CAPACITY_PERCENT_MAX:
        return None, (f"Capacity must be between {CAPACITY_PERCENT_MIN}% and {CAPACITY_PERCENT_MAX}%", 400)
    return value, None


def parse_cost_per_day(raw):
    """(cost, error) for the per-day rate field: blank means "not tracked"."""
    if raw is None or not str(raw).strip():
        return None, None
    text = str(raw).strip().replace(",", ".")
    try:
        value = round(float(text), 2)
    except (TypeError, ValueError):
        return None, ("Cost per day must be a number, e.g. 480 or 480.50", 400)
    if value < 0 or value > COST_PER_DAY_MAX:
        return None, (f"Cost per day must be between 0 and {COST_PER_DAY_MAX:,.2f}", 400)
    return value, None


def update_member_capacity(project, member_id, role=None, capacity_percent=None, cost_per_day=None):
    """Save availability for one resource. Names move tasks; these fields don't."""
    member = TeamMember.query.filter_by(id=member_id, project_id=project.id).first_or_404()
    capacity, error = parse_capacity_percent(capacity_percent)
    if error:
        return None, error
    cost, error = parse_cost_per_day(cost_per_day)
    if error:
        return None, error
    role = (role or "").strip()[:ROLE_MAX_LENGTH] or None
    member.role = role
    member.capacity_percent = capacity
    member.cost_per_day = cost
    db.session.commit()
    return member, None


def delete_team_member(project, member_id):
    member = TeamMember.query.filter_by(id=member_id, project_id=project.id).first_or_404()
    for task in Task.query.filter_by(project_id=project.id, responsible=member.name):
        task.responsible = None
    db.session.delete(member)
    db.session.commit()
    return None, None


@bp.route("/projects/<int:id>/members", methods=["POST"])
def manage_members(id):
    proj = Project.query.get_or_404(id)
    action = request.values.get("action")
    member_id = request.form.get("member_id", type=int)
    error = None

    if action == "add":
        _, error = add_team_member(proj, request.form.get("name"), request.form.get("capacity_percent"))
    elif action == "rename":
        _, error = rename_team_member(proj, member_id, request.form.get("name"))
    elif action == "capacity":
        _, error = update_member_capacity(
            proj,
            member_id,
            role=request.form.get("role"),
            capacity_percent=request.form.get("capacity_percent"),
            cost_per_day=request.form.get("cost_per_day"),
        )
    elif action == "delete":
        _, error = delete_team_member(proj, member_id)
    else:
        return "Unknown team member action", 400

    if error:
        return error

    if request.form.get("partial") == "settings":
        return render_template(
            "partials/settings_members.html",
            project=proj,
            members=members_in_order(proj.id),
            member_roles=member_roles_in_use(proj.id),
        )

    return redirect(url_for("main.project", id=proj.id) + "#settings")


# ---------------------------------------------------------------------------
# Calendar & Holidays
# ---------------------------------------------------------------------------

WEEKDAY_CHOICES = [(i, WEEKDAY_NAMES[i]) for i in range(7)]


def parse_holiday_date(raw, default_year=None):
    """Holiday dates accept the same flexible typing as any other settings date."""
    return parse_flexible_date(raw, default_year=default_year)


@bp.route("/projects/<int:id>/workdays", methods=["POST"])
def update_project_workdays(id):
    proj = Project.query.get_or_404(id)
    raw_days = request.form.getlist("workdays")
    valid_nums = []
    for d_str in raw_days:
        try:
            val = int(d_str)
            if 0 <= val <= 6 and val not in valid_nums:
                valid_nums.append(val)
        except (ValueError, TypeError):
            continue

    if not valid_nums:
        # Prevent completely empty schedule calendar; default to Mon-Fri
        valid_nums = [0, 1, 2, 3, 4]

    valid_nums.sort()
    proj.workdays = ",".join(str(n) for n in valid_nums)
    db.session.commit()

    recalculate_project(proj.id)
    db.session.commit()

    if request.headers.get("HX-Request") or request.form.get("partial") == "settings":
        response = make_response(render_template(
            "partials/settings_workdays.html",
            project=proj,
            weekday_choices=WEEKDAY_CHOICES,
        ))
        response.headers["HX-Trigger-After-Swap"] = "refreshStats, refreshDiagram, refreshGrid"
        return response

    return redirect(url_for("main.project", id=proj.id) + "#settings")


@bp.route("/projects/<int:id>/holidays", methods=["POST"])
def manage_holidays(id):
    proj = Project.query.get_or_404(id)
    action = request.form.get("action", "add")

    if action == "add":
        name = request.form.get("name", "").strip()
        date_raw = request.form.get("date", "").strip()
        if not name:
            return "Holiday name is required", 400
        holiday_date = parse_holiday_date(date_raw)
        if not holiday_date:
            return "A valid holiday date is required (e.g. 2026-12-25 or Dec 25)", 400

        existing = Holiday.query.filter_by(project_id=proj.id, date=holiday_date).first()
        if existing:
            existing.name = name
        else:
            db.session.add(Holiday(project_id=proj.id, date=holiday_date, name=name))
        db.session.commit()
    elif action == "delete":
        holiday_id = request.form.get("holiday_id", type=int)
        h = Holiday.query.filter_by(id=holiday_id, project_id=proj.id).first_or_404()
        db.session.delete(h)
        db.session.commit()

    recalculate_project(proj.id)
    db.session.commit()

    if request.headers.get("HX-Request") or request.form.get("partial") == "settings":
        response = make_response(render_template(
            "partials/settings_holidays.html",
            project=proj,
        ))
        response.headers["HX-Trigger-After-Swap"] = "refreshStats, refreshDiagram, refreshGrid"
        return response

    return redirect(url_for("main.project", id=proj.id) + "#settings")

