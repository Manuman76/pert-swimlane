"""Settings routes: lanes and team members management."""

from flask import render_template, request, redirect, url_for

from ..db import db
from ..models import Project, Lane, TeamMember, Task
from ..services.common import lanes_in_order, members_in_order
from .context import bp


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

def add_team_member(project, name):
    name = (name or "").strip()
    if not name:
        return None, ("Team member name is required", 400)
    if TeamMember.query.filter(
        TeamMember.project_id == project.id,
        db.func.lower(TeamMember.name) == name.lower(),
    ).first():
        return None, ("That team member already exists", 400)
    member = TeamMember(project_id=project.id, name=name)
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
        _, error = add_team_member(proj, request.form.get("name"))
    elif action == "rename":
        _, error = rename_team_member(proj, member_id, request.form.get("name"))
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
        )

    return redirect(url_for("main.project", id=proj.id) + "#settings")
