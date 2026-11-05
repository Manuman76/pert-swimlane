from datetime import datetime, date
from collections import deque

from flask import Blueprint, render_template, request, redirect, url_for, abort, make_response
from .db import db
from .models import (
    Project,
    Lane,
    Task,
    TeamMember,
    Requirement,
    RaidItem,
    MOSCOW_CHOICES,
    MOSCOW_VALUES,
    DEFAULT_MOSCOW,
    RAID_KINDS,
    RAID_KIND_LABELS,
    RAID_KIND_RISK,
    RAID_KIND_ASSUMPTION,
    RAID_KIND_ISSUE,
    RAID_KIND_DECISION,
    RAID_CODE_PREFIX,
    RAID_STATUS_CHOICES,
    RAID_STATUS_VALUES,
    RAID_STATUS_OPEN,
    RAID_LEVEL_CHOICES,
    RAID_LEVEL_VALUES,
    PRIORITY_CHOICES,
    PRIORITY_VALUES,
    DEFAULT_PRIORITY,
    TASK_KIND_TASK,
    TASK_KIND_MILESTONE,
)
from .workdays import count_workdays, next_workday
from .task_tree import build_task_tree_rows
from .project_overview import build_project_overview, fmt_overview_day
from .services.scheduler import (
    recalculate_project,
    validate_task,
    validate_predecessors_change as _validate_predecessors_change,
    project_date_stats,
    task_successors,
)

bp = Blueprint("main", __name__)


def _task_table_render_kwargs(project, tasks, **extra):
    """Shared context for the task grid partial (hierarchical row order)."""
    return {
        "project": project,
        "lanes": lanes_in_order(project.id),
        "tasks": tasks,
        "task_rows": build_task_tree_rows(tasks),
        "members": members_in_order(project.id),
        "requirements": requirements_in_order(project.id),
        "fmt_date": fmt_date,
        **extra,
    }


def _parse_moscow(raw):
    value = (raw or "").strip().lower()
    return value if value in MOSCOW_VALUES else DEFAULT_MOSCOW


def fmt_date(d):
    return d.strftime("%d %b %Y") if d else ""


def parse_date(s):
    """Parse an entered date, rolled forward onto a working day.

    The calendar is a 5-day week: a start date typed on a Saturday or Sunday
    means "when the work actually starts", i.e. the following Monday, so the
    grid never stores a weekend date.
    """
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%d %b %Y", "%d %B %Y"):
        try:
            return next_workday(datetime.strptime(s.strip(), fmt).date())
        except ValueError:
            continue
    return None


def _normalize_rich_text(value):
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


# Projects index
def get_project_stats(project):
    return {
        "id": project.id,
        "name": project.name,
        "updated_at": project.updated_at,
        "task_count": len(project.tasks),
    }


@bp.route("/")
def index():
    projects = Project.query.order_by(Project.updated_at.desc()).all()
    return render_template("index.html", projects=[get_project_stats(p) for p in projects])


@bp.route("/projects", methods=["POST"])
def create_project():
    name = request.form.get("name", "").strip()
    if not name:
        return "Project name is required", 400
    proj = Project(name=name)
    db.session.add(proj)
    db.session.commit()
    return redirect(url_for("main.index"))


@bp.route("/projects/<int:id>/rename", methods=["POST"])
def rename_project(id):
    proj = Project.query.get_or_404(id)
    name = request.form.get("name", "").strip()
    if not name:
        return "Project name is required", 400
    proj.name = name
    db.session.commit()
    return redirect(url_for("main.index"))


@bp.route("/projects/<int:id>/delete", methods=["POST"])
def delete_project(id):
    proj = Project.query.get_or_404(id)
    db.session.delete(proj)
    db.session.commit()
    return redirect(url_for("main.index"))


def _swap_lane_orders(a, b):
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


# Lanes
@bp.route("/projects/<int:id>/lanes", methods=["POST"])
def manage_lanes(id):
    """Manage the project's configured swimlanes from Project settings."""
    proj = Project.query.get_or_404(id)
    action = request.values.get("action")

    if action == "add":
        name = request.form.get("name", "").strip()
        if not name:
            return "Lane name is required", 400
        max_order = db.session.query(db.func.max(Lane.sort_order)).filter_by(project_id=proj.id).scalar() or 0
        lane = Lane(project_id=proj.id, name=name, sort_order=max_order + 1)
        db.session.add(lane)
        db.session.commit()
    elif action == "rename":
        lane_id = request.form.get("lane_id", type=int)
        name = request.form.get("name", "").strip()
        lane = Lane.query.filter_by(id=lane_id, project_id=proj.id).first_or_404()
        if name:
            lane.name = name
            db.session.commit()
    elif action == "move_up":
        lane_id = request.form.get("lane_id", type=int)
        lane = Lane.query.filter_by(id=lane_id, project_id=proj.id).first_or_404()
        above = Lane.query.filter(Lane.project_id == proj.id, Lane.sort_order < lane.sort_order).order_by(Lane.sort_order.desc()).first()
        if above:
            _swap_lane_orders(lane, above)
    elif action == "move_down":
        lane_id = request.form.get("lane_id", type=int)
        lane = Lane.query.filter_by(id=lane_id, project_id=proj.id).first_or_404()
        below = Lane.query.filter(Lane.project_id == proj.id, Lane.sort_order > lane.sort_order).order_by(Lane.sort_order).first()
        if below:
            _swap_lane_orders(lane, below)
    elif action == "delete":
        lane_id = request.form.get("lane_id", type=int)
        lane = Lane.query.filter_by(id=lane_id, project_id=proj.id).first_or_404()
        if lane.tasks:
            remaining = Lane.query.filter(Lane.project_id == proj.id, Lane.id != lane.id).order_by(Lane.sort_order).first()
            if not remaining:
                return "Cannot delete the last lane with tasks. Create another lane first.", 400
            for t in lane.tasks:
                t.lane_id = remaining.id
        db.session.delete(lane)
        db.session.commit()
        lanes = Lane.query.filter_by(project_id=proj.id).order_by(Lane.sort_order).all()
        for i, l in enumerate(lanes, 1):
            l.sort_order = i
        db.session.commit()

    if request.form.get("partial") == "settings":
        return render_template(
            "partials/settings_lanes.html",
            project=proj,
            lanes=lanes_in_order(proj.id),
        )

    fragment = "#settings" if request.form.get("tab") == "settings" else ""
    return redirect(url_for("main.project", id=proj.id) + fragment)


# Tasks helpers
# ---------------------------------------------------------------------------

def next_task_code(project_id):
    max_code = db.session.query(db.func.max(Task.code)).filter_by(project_id=project_id).scalar()
    if max_code:
        next_num = int(max_code) + 1
    else:
        next_num = 1
    return f"{next_num:03d}"


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


def _overview_context(project, tasks=None):
    if tasks is None:
        tasks = Task.query.filter_by(project_id=project.id).all()
    requirements = requirements_in_order(project.id)
    raid_items = RaidItem.query.filter_by(project_id=project.id).all()
    return {
        "overview": build_project_overview(tasks, requirements, raid_items),
        "fmt_overview_day": fmt_overview_day,
    }


def _requirements_partial_context(project):
    return {
        "project": project,
        "requirements": requirements_in_order(project.id),
        "moscow_choices": MOSCOW_CHOICES,
    }


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


def _parse_raid_kind(raw):
    kind = (raw or "").strip().lower()
    return kind if kind in RAID_KINDS else None


def _parse_raid_status(raw, default=RAID_STATUS_OPEN):
    value = (raw or default).strip().lower()
    return value if value in RAID_STATUS_VALUES else default


def _parse_raid_level(raw):
    if raw is None or raw == "":
        return None
    value = raw.strip().lower()
    return value if value in RAID_LEVEL_VALUES else None


def _raid_context(project):
    items = RaidItem.query.filter_by(project_id=project.id).order_by(
        RaidItem.kind, RaidItem.code
    ).all()
    by_kind = {kind: [] for kind in RAID_KINDS}
    for item in items:
        by_kind[item.kind].append(item)
    return {
        "raid_by_kind": by_kind,
        "raid_kinds": RAID_KINDS,
        "raid_kind_labels": RAID_KIND_LABELS,
        "raid_status_choices": RAID_STATUS_CHOICES,
        "raid_level_choices": RAID_LEVEL_CHOICES,
    }


def _set_tasks_predecessors(project_id, task_ids, raw_predecessors):
    """Update predecessors on many tasks; recalculate once. Returns ``errors`` dict."""
    proj = Project.query.get_or_404(project_id)
    errors = {}
    any_change = False
    seen_ids = set()

    for raw_id in task_ids:
        try:
            tid = int(raw_id)
        except (TypeError, ValueError):
            continue
        if tid in seen_ids:
            continue
        seen_ids.add(tid)
        task = Task.query.filter_by(id=tid, project_id=proj.id).first()
        if not task:
            continue
        new_preds, task_errors = _validate_predecessors_change(task, proj.id, raw_predecessors)
        if task_errors:
            errors[task.id] = task_errors
            continue
        if (new_preds or "") != (task.predecessors or ""):
            task.predecessors = new_preds
            any_change = True

    if any_change:
        recalculate_project(proj.id)
        db.session.commit()

    return errors


def _task_table_swap_response(proj, tasks, **extra):
    response = make_response(render_template(
        "partials/task_table_body.html",
        **_task_table_render_kwargs(proj, tasks, **extra),
    ))
    response.headers["HX-Trigger-After-Swap"] = "refreshStats, refreshDiagram, refreshRequirements"
    return response


# Routes for tasks
# ---------------------------------------------------------------------------


@bp.route("/projects/<int:id>/requirements", methods=["POST"])
def manage_requirements(id):
    proj = Project.query.get_or_404(id)
    action = request.form.get("action")
    requirement_id = request.form.get("requirement_id", type=int)

    if action == "add":
        title = request.form.get("title", "").strip()
        if not title:
            return "Requirement title is required", 400
        requirement = Requirement(
            project_id=proj.id,
            code=next_requirement_code(proj.id),
            title=title,
            description=request.form.get("description", "").strip() or None,
            moscow=_parse_moscow(request.form.get("moscow")),
            status=request.form.get("status", "open"),
        )
        db.session.add(requirement)
    elif action in ("update", "delete"):
        requirement = Requirement.query.filter_by(
            id=requirement_id, project_id=proj.id
        ).first_or_404()
        if action == "delete":
            db.session.delete(requirement)
        else:
            title = request.form.get("title", "").strip()
            if not title:
                return "Requirement title is required", 400
            requirement.title = title
            requirement.description = request.form.get("description", "").strip() or None
            requirement.moscow = _parse_moscow(request.form.get("moscow"))
    else:
        return "Unknown requirement action", 400

    db.session.commit()
    if request.form.get("partial") == "requirements":
        return render_template(
            "partials/requirements.html",
            **_requirements_partial_context(proj),
        )
    return redirect(url_for("main.project", id=proj.id) + "#requirements")


@bp.route("/projects/<int:id>/requirements/list")
def requirements_list_partial(id):
    proj = Project.query.get_or_404(id)
    return render_template(
        "partials/requirements.html",
        **_requirements_partial_context(proj),
    )


@bp.route("/projects/<int:id>/raid", methods=["POST"])
def manage_raid(id):
    proj = Project.query.get_or_404(id)
    action = request.form.get("action")
    raid_id = request.form.get("raid_id", type=int)
    kind = _parse_raid_kind(request.form.get("kind"))

    if action == "add":
        if not kind:
            return "RAID item type is required", 400
        title = request.form.get("title", "").strip()
        if not title:
            return "Title is required", 400
        item = RaidItem(
            project_id=proj.id,
            kind=kind,
            code=next_raid_code(proj.id, kind),
            title=title,
            description=request.form.get("description", "").strip() or None,
            owner=request.form.get("owner", "").strip() or None,
            status=_parse_raid_status(request.form.get("status")),
        )
        if kind == RAID_KIND_RISK:
            item.probability = _parse_raid_level(request.form.get("probability"))
            item.impact = _parse_raid_level(request.form.get("impact"))
            item.mitigation = request.form.get("mitigation", "").strip() or None
        elif kind == RAID_KIND_ISSUE:
            item.severity = _parse_raid_level(request.form.get("severity"))
            due_raw = request.form.get("due_date", "").strip()
            item.due_date = parse_date(due_raw) if due_raw else None
        elif kind == RAID_KIND_DECISION:
            date_raw = request.form.get("decision_date", "").strip()
            item.decision_date = parse_date(date_raw) if date_raw else None
            item.decision_text = request.form.get("decision_text", "").strip() or None
            item.rationale = request.form.get("rationale", "").strip() or None
        db.session.add(item)
    elif action in ("update", "delete"):
        item = RaidItem.query.filter_by(id=raid_id, project_id=proj.id).first_or_404()
        if action == "delete":
            db.session.delete(item)
        else:
            title = request.form.get("title", "").strip()
            if not title:
                return "Title is required", 400
            item.title = title
            item.description = request.form.get("description", "").strip() or None
            owner_raw = request.form.get("owner")
            item.owner = owner_raw.strip() if owner_raw else None
            if item.owner == "":
                item.owner = None
            item.status = _parse_raid_status(request.form.get("status"), item.status)
            if item.kind == RAID_KIND_RISK:
                item.probability = _parse_raid_level(request.form.get("probability"))
                item.impact = _parse_raid_level(request.form.get("impact"))
                item.mitigation = request.form.get("mitigation", "").strip() or None
            elif item.kind == RAID_KIND_ISSUE:
                item.severity = _parse_raid_level(request.form.get("severity"))
                due_raw = request.form.get("due_date", "").strip()
                item.due_date = parse_date(due_raw) if due_raw else None
            elif item.kind == RAID_KIND_DECISION:
                date_raw = request.form.get("decision_date", "").strip()
                item.decision_date = parse_date(date_raw) if date_raw else None
                item.decision_text = request.form.get("decision_text", "").strip() or None
                item.rationale = request.form.get("rationale", "").strip() or None
    else:
        return "Unknown RAID action", 400

    db.session.commit()
    if request.form.get("partial") == "raid":
        ctx = _raid_context(proj)
        ctx.update({
            "project": proj,
            "members": members_in_order(proj.id),
            "fmt_date": fmt_date,
        })
        return render_template("partials/raid_pane.html", **ctx)
    return redirect(url_for("main.project", id=proj.id) + "#raid")


@bp.route("/projects/<int:id>", methods=["GET"])
def project(id):
    proj = Project.query.get_or_404(id)
    lanes = lanes_in_order(proj.id)
    tasks = Task.query.filter_by(project_id=proj.id).all()
    total_effort, total_duration, project_start, project_end = project_date_stats(tasks)
    table_ctx = _task_table_render_kwargs(proj, tasks)
    return render_template("project.html", project=proj, lanes=lanes, tasks=tasks,
                           task_rows=table_ctx["task_rows"],
                           members=table_ctx["members"],
                           requirements=table_ctx["requirements"],
                           moscow_choices=MOSCOW_CHOICES,
                           fmt_date=fmt_date, total_effort=total_effort,
                           total_duration=total_duration,
                           project_start=project_start, project_end=project_end,
                           **_raid_context(proj),
                           **_overview_context(proj, tasks))


@bp.route("/projects/<int:id>/overview")
def project_overview_partial(id):
    proj = Project.query.get_or_404(id)
    tasks = Task.query.filter_by(project_id=proj.id).all()
    return render_template(
        "partials/project_overview.html",
        project=proj,
        **_overview_context(proj, tasks),
    )


@bp.route("/projects/<int:id>/settings", methods=["POST"])
def update_project_settings(id):
    proj = Project.query.get_or_404(id)
    name = request.form.get("name", "").strip()
    if not name:
        return "Project name is required", 400
    if Project.query.filter(Project.name == name, Project.id != proj.id).first():
        return "A project with that name already exists", 400

    proj.name = name
    proj.description = _normalize_rich_text(request.form.get("description", ""))
    db.session.commit()
    return redirect(url_for("main.project", id=proj.id) + "#settings")


@bp.route("/projects/<int:id>/members", methods=["POST"])
def manage_members(id):
    proj = Project.query.get_or_404(id)
    action = request.values.get("action")
    member_id = request.form.get("member_id", type=int)

    if action == "add":
        name = request.form.get("name", "").strip()
        if not name:
            return "Team member name is required", 400
        if TeamMember.query.filter(
            TeamMember.project_id == proj.id,
            db.func.lower(TeamMember.name) == name.lower(),
        ).first():
            return "That team member already exists", 400
        db.session.add(TeamMember(project_id=proj.id, name=name))
        db.session.commit()
    elif action == "rename":
        member = TeamMember.query.filter_by(id=member_id, project_id=proj.id).first_or_404()
        name = request.form.get("name", "").strip()
        if not name:
            return "Team member name is required", 400
        duplicate = TeamMember.query.filter(
            TeamMember.project_id == proj.id,
            TeamMember.id != member.id,
            db.func.lower(TeamMember.name) == name.lower(),
        ).first()
        if duplicate:
            return "That team member already exists", 400
        old_name = member.name
        member.name = name
        for task in Task.query.filter_by(project_id=proj.id, responsible=old_name):
            task.responsible = name
        db.session.commit()
    elif action == "delete":
        member = TeamMember.query.filter_by(id=member_id, project_id=proj.id).first_or_404()
        for task in Task.query.filter_by(project_id=proj.id, responsible=member.name):
            task.responsible = None
        db.session.delete(member)
        db.session.commit()
    else:
        return "Unknown team member action", 400

    if request.form.get("partial") == "settings":
        return render_template(
            "partials/settings_members.html",
            project=proj,
            members=members_in_order(proj.id),
        )

    return redirect(url_for("main.project", id=proj.id) + "#settings")


@bp.route("/projects/<int:id>/tasks", methods=["POST"])
def create_task(id):
    proj = Project.query.get_or_404(id)

    # Lanes come out of the grid now, so a brand-new project has none: give the
    # first task a lane to live in rather than refusing to add it.
    lanes = lanes_in_order(proj.id)
    if not lanes:
        first = Lane(project_id=proj.id, name="Lane 1", sort_order=1)
        db.session.add(first)
        db.session.flush()
        lanes = [first]

    lane_name = request.form.get("lane_name", "").strip()
    if lane_name:
        lane = Lane.query.filter(
            Lane.project_id == proj.id,
            db.func.lower(Lane.name) == lane_name.lower(),
        ).first()
        if lane is None:
            return "Lane must be selected from the project's swimlanes", 400
    else:
        # Otherwise keep the new row in the lane of the last row, so clicking
        # "+ Add task" several times groups the tasks in the same swimlane.
        last = Task.query.filter_by(project_id=proj.id).order_by(Task.code.desc()).first()
        lane = (Lane.query.filter_by(id=last.lane_id, project_id=proj.id).first()
                if last else None) or lanes[-1]

    kind = request.form.get("kind", TASK_KIND_TASK).strip().lower()
    if kind not in (TASK_KIND_TASK, TASK_KIND_MILESTONE):
        kind = TASK_KIND_TASK
    is_milestone = kind == TASK_KIND_MILESTONE

    task = Task(
        project_id=proj.id,
        code=next_task_code(proj.id),
        kind=kind,
        lane_id=lane.id,
        name="New Milestone" if is_milestone else "New Task",
        start_date=next_workday(date.today()),
        duration_days=0 if is_milestone else 1,
        responsible=None,
        predecessors=None,
    )
    db.session.add(task)
    db.session.commit()

    # A new task may shift successors (e.g. it is referenced as a predecessor
    # by an existing task, or sits in the chain). Recalculate so the grid is
    # consistent immediately.
    recalculate_project(proj.id)
    db.session.commit()

    lanes = lanes_in_order(proj.id)
    tasks = Task.query.filter_by(project_id=proj.id).all()
    response = make_response(render_template(
        "partials/task_table_body.html",
        **_task_table_render_kwargs(proj, tasks),
    ))
    response.headers["HX-Trigger-After-Swap"] = "refreshStats, refreshDiagram, refreshRequirements"
    return response


@bp.route("/tasks/<int:task_id>/update", methods=["POST"])
def update_task(task_id):
    task = Task.query.get_or_404(task_id)
    proj = Project.query.get_or_404(task.project_id)
    lanes = lanes_in_order(proj.id)

    # Gather new values
    new_name = request.form.get("name", task.name).strip()

    # The grid posts the selected lane name. `lane_id` remains accepted for
    # older clients that do not send the current dropdown field.
    lane_name_raw = request.form.get("lane_name")
    lane_error = None
    if lane_name_raw is None:
        new_lane_name = None
        new_lane_id = request.form.get("lane_id", type=int) or task.lane_id
    else:
        new_lane_name = lane_name_raw.strip()
        selected_lane = Lane.query.filter(
            Lane.project_id == proj.id,
            db.func.lower(Lane.name) == new_lane_name.lower(),
        ).first() if new_lane_name else None
        new_lane_id = selected_lane.id if selected_lane else task.lane_id
        if new_lane_name and selected_lane is None:
            lane_error = "Lane must be selected from the project's swimlanes."

    start_raw = request.form.get("start_date", "").strip()
    new_start = parse_date(start_raw) if start_raw else task.start_date

    dur_raw = request.form.get("duration_days", "").strip()
    if task.kind == TASK_KIND_MILESTONE:
        new_dur = 0
    elif dur_raw:
        if dur_raw.isdigit():
            new_dur = int(dur_raw)
        else:
            new_dur = -1  # sentinel for validation failure
    else:
        new_dur = task.duration_days

    responsible_raw = request.form.get("responsible")
    new_resp = (
        responsible_raw.strip() if responsible_raw is not None
        else (task.responsible or "")
    ) or None
    requirement_raw = request.form.get("requirement_id")
    selected_requirement = task.requirements[0] if requirement_raw is None and task.requirements else None
    if requirement_raw:
        selected_requirement = Requirement.query.filter_by(
            id=request.form.get("requirement_id", type=int), project_id=proj.id
        ).first()
        if selected_requirement is None:
            lane_error = lane_error or "Business requirement must belong to this project."
    if "predecessors" in request.form:
        new_preds = request.form.get("predecessors", "").strip() or None
    else:
        new_preds = task.predecessors
    # Status dropdown: 'not_started' | 'started' | 'done'. If absent, keep current state.
    status_value = request.form.get("status")
    if status_value not in ("not_started", "started", "done"):
        status_value = task.status
    started_by_status = status_value in ("started", "done")
    done_by_status = status_value == "done"

    # Validate using a temporary task without touching the real one.
    temp = Task(
        project_id=proj.id,
        code=task.code,
        kind=task.kind,
        lane_id=new_lane_id,
        name=new_name,
        start_date=new_start,
        duration_days=new_dur,
        responsible=new_resp,
        predecessors=new_preds,
        started=started_by_status,
        done=done_by_status,
    )

    # The scheduling rule (start >= max(pred.end)) is only a blocking error
    # when the user is actively moving the start date earlier. If they merely
    # change the predecessor set and leave the stored start date alone,
    # recalculation below pushes the date forward instead of rejecting.
    start_moved_earlier = bool(new_start) and new_start < task.start_date
    preds_changed = (new_preds or "") != (task.predecessors or "")
    check_schedule = start_moved_earlier or not preds_changed
    errors = validate_task(temp, proj.id, exclude_task_id=task.id, check_schedule=check_schedule)
    if lane_error:
        errors.append(lane_error)
    if responsible_raw is not None and new_resp:
        member_names = {member.name for member in members_in_order(proj.id)}
        if new_resp not in member_names and new_resp != task.responsible:
            errors.append("Responsible must be an available team member.")

    if errors:
        tasks = Task.query.filter_by(project_id=proj.id).all()
        response = make_response(render_template(
            "partials/task_table_body.html",
            **_task_table_render_kwargs(
                proj,
                tasks,
                errors={task.id: errors},
                drafts={
                    task.id: {
                        "name": new_name,
                        "lane_id": new_lane_id,
                        "lane_name": new_lane_name or None,
                        "start_date": new_start if new_start else None,
                        "duration_days": new_dur if new_dur >= 0 else None,
                        "responsible": new_resp,
                        "requirement_id": request.form.get("requirement_id", type=int),
                        "predecessors": new_preds,
                    }
                },
            ),
        ))
        response.headers["HX-Trigger-After-Swap"] = "refreshDiagram"
        return response, 422

    task.name = new_name
    task.lane_id = new_lane_id
    task.start_date = new_start
    task.duration_days = new_dur
    task.responsible = new_resp
    task.requirements = [selected_requirement] if selected_requirement else []
    task.predecessors = new_preds
    task.started = started_by_status
    task.done = done_by_status

    # Push successors forward so the whole chain stays consistent.
    recalculate_project(proj.id)
    db.session.commit()

    lanes = lanes_in_order(proj.id)
    tasks = Task.query.filter_by(project_id=proj.id).all()
    response = make_response(render_template(
        "partials/task_table_body.html",
        **_task_table_render_kwargs(proj, tasks),
    ))
    response.headers["HX-Trigger-After-Swap"] = "refreshStats, refreshDiagram, refreshRequirements"
    return response


@bp.route("/projects/<int:id>/tasks/predecessors", methods=["POST"])
def set_tasks_predecessors(id):
    """Bulk-set predecessors for drag-and-drop multi-select in the task grid."""
    proj = Project.query.get_or_404(id)
    if "predecessors" not in request.form:
        return "Missing predecessors field", 400
    raw = request.form.get("predecessors")
    task_ids = request.form.getlist("task_ids")
    if not task_ids:
        single = request.form.get("task_ids", "").strip()
        if single:
            task_ids = [p.strip() for p in single.split(",") if p.strip()]
    if not task_ids:
        return "Missing task_ids", 400

    errors = _set_tasks_predecessors(proj.id, task_ids, raw)
    tasks = Task.query.filter_by(project_id=proj.id).all()
    extra = {"errors": errors} if errors else {}
    response = _task_table_swap_response(proj, tasks, **extra)
    if errors:
        response.headers["HX-Trigger-After-Swap"] = "refreshDiagram"
        return response, 422
    return response


@bp.route("/tasks/<int:task_id>/predecessors", methods=["POST"])
def set_task_predecessors(task_id):
    """Set comma-separated predecessors (used by drag-and-drop in the task grid)."""
    task = Task.query.get_or_404(task_id)
    proj = Project.query.get_or_404(task.project_id)
    raw = request.form.get("predecessors")
    if raw is None:
        return "Missing predecessors field", 400

    errors = _set_tasks_predecessors(proj.id, [str(task.id)], raw)
    tasks = Task.query.filter_by(project_id=proj.id).all()
    if errors.get(task.id):
        response = _task_table_swap_response(
            proj,
            tasks,
            errors={task.id: errors[task.id]},
        )
        response.headers["HX-Trigger-After-Swap"] = "refreshDiagram"
        return response, 422

    return _task_table_swap_response(proj, tasks)


@bp.route("/tasks/<int:task_id>/delete", methods=["POST"])
def delete_task(task_id):
    task = Task.query.get_or_404(task_id)
    proj = Project.query.get_or_404(task.project_id)

    # Remove references to this task from other tasks' predecessors
    code = task.code
    all_tasks = Task.query.filter_by(project_id=proj.id).all()
    for t in all_tasks:
        if t.predecessors and code in t.predecessor_codes():
            codes = [c for c in t.predecessor_codes() if c != code]
            t.predecessors = ", ".join(codes) if codes else None

    db.session.delete(task)
    db.session.commit()

    # Without the removed predecessor, remaining tasks may be pushed forward
    # or their chain may need to re-settle. Recalculate before rendering so
    # the grid shows the persisted dates.
    recalculate_project(proj.id)
    db.session.commit()

    lanes = lanes_in_order(proj.id)
    tasks = Task.query.filter_by(project_id=proj.id).all()
    response = make_response(render_template(
        "partials/task_table_body.html",
        **_task_table_render_kwargs(proj, tasks),
    ))
    response.headers["HX-Trigger-After-Swap"] = "refreshStats, refreshDiagram, refreshRequirements"
    return response


# Export
# ---------------------------------------------------------------------------

@bp.route("/projects/<int:id>/export/jira.csv")
def export_jira_csv(id):
    """Download the project's tasks as a CSV that Jira's importer accepts."""
    from .jira_export import export_project_csv, export_filename

    proj = Project.query.get_or_404(id)
    lanes = lanes_in_order(proj.id)
    tasks = Task.query.filter_by(project_id=proj.id).all()
    csv_text = export_project_csv(proj, lanes, tasks)

    response = make_response(csv_text)
    response.headers["Content-Type"] = "text/csv; charset=utf-8"
    response.headers["Content-Disposition"] = (
        f'attachment; filename="{export_filename(proj)}"'
    )
    return response


def task_successors(task, all_tasks):
    code = task.code
    return sorted(
        [t for t in all_tasks if code in t.predecessor_codes()],
        key=lambda t: (t.start_date, t.code),
    )


def _task_detail_context(task, proj):
    from .diagram import STATUS_STYLES

    all_tasks = Task.query.filter_by(project_id=proj.id).all()
    by_code = {t.code: t for t in all_tasks}
    predecessor_tasks = [
        by_code[c] for c in task.predecessor_codes() if c in by_code
    ]
    req = task.requirements[0] if task.requirements else None
    return {
        "task": task,
        "project": proj,
        "predecessor_tasks": predecessor_tasks,
        "successor_tasks": task_successors(task, all_tasks),
        "linked_requirement": req,
        "lanes": lanes_in_order(proj.id),
        "members": members_in_order(proj.id),
        "requirements": requirements_in_order(proj.id),
        "priority_choices": PRIORITY_CHOICES,
        "status_styles": STATUS_STYLES,
        "fmt_date": fmt_date,
    }


@bp.route("/projects/<int:id>/task-detail-empty", methods=["GET"])
def task_detail_empty(id):
    Project.query.get_or_404(id)
    return render_template("partials/task_detail_empty.html")


@bp.route("/tasks/<int:task_id>/detail", methods=["GET"])
def task_detail(task_id):
    task = Task.query.get_or_404(task_id)
    proj = Project.query.get_or_404(task.project_id)
    return render_template(
        "partials/task_detail_panel.html",
        **_task_detail_context(task, proj),
    )


@bp.route("/tasks/<int:task_id>/detail", methods=["POST"])
def update_task_detail(task_id):
    task = Task.query.get_or_404(task_id)
    proj = Project.query.get_or_404(task.project_id)
    errors = []

    new_name = request.form.get("name", task.name).strip()
    if not new_name:
        errors.append("Task name is required.")

    new_description = request.form.get("description", "").strip() or None
    new_notes = request.form.get("notes", "").strip() or None
    new_links = request.form.get("links", "").strip() or None

    new_priority = task.priority or DEFAULT_PRIORITY
    priority_raw = (request.form.get("priority") or new_priority).strip().lower()
    if priority_raw not in PRIORITY_VALUES:
        errors.append("Invalid priority.")
    else:
        new_priority = priority_raw

    progress_raw = request.form.get("progress_percent", "").strip()
    if progress_raw == "":
        new_progress = None
    elif progress_raw.isdigit():
        new_progress = max(0, min(100, int(progress_raw)))
    else:
        errors.append("Progress must be a whole number from 0 to 100.")
        new_progress = task.progress_percent

    lane_name_raw = request.form.get("lane_name", "").strip()
    new_lane_id = task.lane_id
    selected_lane = Lane.query.filter(
        Lane.project_id == proj.id,
        db.func.lower(Lane.name) == lane_name_raw.lower(),
    ).first() if lane_name_raw else None
    if not selected_lane:
        errors.append("Lane must be selected from the project's swimlanes.")
    else:
        new_lane_id = selected_lane.id

    start_raw = request.form.get("start_date", "").strip()
    new_start = parse_date(start_raw) if start_raw else task.start_date

    dur_raw = request.form.get("duration_days", "").strip()
    if task.kind == TASK_KIND_MILESTONE:
        new_dur = 0
    elif dur_raw and dur_raw.isdigit():
        new_dur = int(dur_raw)
    elif dur_raw:
        new_dur = -1
        errors.append("Duration must be an integer >= 1.")
    else:
        new_dur = task.duration_days

    responsible_raw = request.form.get("responsible")
    new_resp = (
        responsible_raw.strip() if responsible_raw is not None
        else (task.responsible or "")
    ) or None
    if new_resp:
        member_names = {m.name for m in members_in_order(proj.id)}
        if new_resp not in member_names and new_resp != task.responsible:
            errors.append("Responsible must be an available team member.")

    requirement_raw = request.form.get("requirement_id")
    selected_requirement = None
    if requirement_raw:
        selected_requirement = Requirement.query.filter_by(
            id=request.form.get("requirement_id", type=int), project_id=proj.id
        ).first()
        if selected_requirement is None:
            errors.append("Business requirement must belong to this project.")

    status_value = request.form.get("status", task.status)
    if status_value not in ("not_started", "started", "done"):
        status_value = task.status
    started_by_status = status_value in ("started", "done")
    done_by_status = status_value == "done"

    temp = Task(
        project_id=proj.id,
        code=task.code,
        kind=task.kind,
        lane_id=new_lane_id if not errors else task.lane_id,
        name=new_name or task.name,
        start_date=new_start,
        duration_days=new_dur if new_dur >= 0 else task.duration_days,
        responsible=new_resp,
        predecessors=task.predecessors,
        started=started_by_status,
        done=done_by_status,
    )
    if not errors:
        errors.extend(validate_task(temp, proj.id, exclude_task_id=task.id, check_schedule=False))

    if errors:
        ctx = _task_detail_context(task, proj)
        ctx["panel_errors"] = errors
        ctx["draft"] = request.form
        response = make_response(render_template("partials/task_detail_panel.html", **ctx))
        return response, 422

    task.name = new_name
    task.description = new_description
    task.notes = new_notes
    task.links = new_links
    task.priority = new_priority
    task.progress_percent = new_progress
    task.lane_id = new_lane_id
    task.start_date = new_start
    task.duration_days = new_dur
    task.responsible = new_resp
    task.requirements = [selected_requirement] if selected_requirement else []
    task.started = started_by_status
    task.done = done_by_status

    recalculate_project(proj.id)
    db.session.commit()

    response = make_response(render_template(
        "partials/task_detail_panel.html",
        **_task_detail_context(task, proj),
    ))
    response.headers["HX-Trigger-After-Swap"] = "refreshStats, refreshDiagram, refreshGrid, refreshRequirements"
    return response


# Diagram partials
# ---------------------------------------------------------------------------

@bp.route("/projects/<int:id>/diagram")
def diagram_partial(id):
    proj = Project.query.get_or_404(id)
    lanes = lanes_in_order(proj.id)
    tasks = Task.query.filter_by(project_id=proj.id).all()
    from .diagram import build_diagram
    return build_diagram(lanes, tasks)
