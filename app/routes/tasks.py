"""Task management, predecessor assignment, drawer, and tree routes."""

import datetime
from flask import render_template, request, make_response

from ..db import db
from ..models import (
    Project,
    Lane,
    Task,
    Requirement,
    TASK_KIND_TASK,
    TASK_KIND_MILESTONE,
    PRIORITY_CHOICES,
)
from ..services.common import (
    fmt_date,
    parse_date,
    normalize_rich_text,
    lanes_in_order,
    members_in_order,
    requirements_in_order,
    next_task_code,
)
from ..services.scheduler import (
    recalculate_project,
    validate_task,
    validate_predecessors_change,
    task_successors,
)
from ..workdays import next_workday
from .context import bp, task_table_render_kwargs, task_table_swap_response


def set_tasks_predecessors_helper(project_id, task_ids, raw_predecessors):
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
        new_preds, task_errors = validate_predecessors_change(task, proj.id, raw_predecessors)
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


def task_detail_context(task, proj):
    from ..diagram import STATUS_STYLES

    all_tasks = Task.query.filter_by(project_id=proj.id).all()
    by_code = {t.code: t for t in all_tasks}
    predecessor_tasks = [
        by_code[code] for code in task.predecessor_codes()
        if code in by_code
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

    from . import date as _routes_date
    _today_fn = getattr(_routes_date, "today", datetime.date.today)

    task = Task(
        project_id=proj.id,
        code=next_task_code(proj.id),
        kind=kind,
        lane_id=lane.id,
        name="New Milestone" if is_milestone else "New Task",
        start_date=next_workday(_today_fn(), calendar=proj.calendar),
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

    tasks = Task.query.filter_by(project_id=proj.id).all()
    return task_table_swap_response(proj, tasks)


@bp.route("/tasks/<int:task_id>/update", methods=["POST"])
def update_task(task_id):
    task = Task.query.get_or_404(task_id)
    proj = Project.query.get_or_404(task.project_id)

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
    new_start = parse_date(start_raw, calendar=proj.calendar) if start_raw else task.start_date

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
            **task_table_render_kwargs(
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
    db.session.commit()

    recalculate_project(proj.id)
    db.session.commit()

    tasks = Task.query.filter_by(project_id=proj.id).all()
    return task_table_swap_response(proj, tasks)


@bp.route("/projects/<int:id>/tasks/predecessors", methods=["POST"])
def set_tasks_predecessors(id):
    """Bulk update predecessors for multiple tasks (e.g. multi-select drag-and-drop)."""
    proj = Project.query.get_or_404(id)
    raw_task_ids = request.form.getlist("task_ids")
    if not raw_task_ids:
        raw_task_ids = [t.strip() for t in request.form.get("task_ids", "").split(",") if t.strip()]
    raw_predecessors = request.form.get("predecessors", "")

    errors = set_tasks_predecessors_helper(proj.id, raw_task_ids, raw_predecessors)
    tasks = Task.query.filter_by(project_id=proj.id).all()
    if errors:
        response = make_response(render_template(
            "partials/task_table_body.html",
            **task_table_render_kwargs(proj, tasks, errors=errors),
        ))
        response.headers["HX-Trigger-After-Swap"] = "refreshDiagram"
        return response, 422

    return task_table_swap_response(proj, tasks)


@bp.route("/tasks/<int:task_id>/predecessors", methods=["POST"])
def set_task_predecessors(task_id):
    """Dependency-only update invoked by diagram and task list drag-and-drop."""
    task = Task.query.get_or_404(task_id)
    proj = Project.query.get_or_404(task.project_id)
    raw = request.form.get("predecessors", "")
    new_preds, errors = validate_predecessors_change(task, proj.id, raw)

    if errors:
        tasks = Task.query.filter_by(project_id=proj.id).all()
        response = make_response(render_template(
            "partials/task_table_body.html",
            **task_table_render_kwargs(proj, tasks, errors={task.id: errors}),
        ))
        response.headers["HX-Trigger-After-Swap"] = "refreshDiagram"
        return response, 422

    task.predecessors = new_preds
    db.session.commit()

    recalculate_project(proj.id)
    db.session.commit()

    tasks = Task.query.filter_by(project_id=proj.id).all()
    return task_table_swap_response(proj, tasks)


@bp.route("/tasks/<int:task_id>/delete", methods=["POST"])
def delete_task(task_id):
    task = Task.query.get_or_404(task_id)
    proj = Project.query.get_or_404(task.project_id)
    deleted_code = task.code

    db.session.delete(task)
    db.session.commit()

    # Cascade: remove this task's code from any successor's predecessor list
    for t in Task.query.filter_by(project_id=proj.id).all():
        codes = t.predecessor_codes()
        if deleted_code in codes:
            codes.remove(deleted_code)
            t.predecessors = ", ".join(codes) if codes else None
    db.session.commit()

    # Re-settle dates: with a predecessor removed, remaining dependencies may
    # still push dates forward.
    recalculate_project(proj.id)
    db.session.commit()

    tasks = Task.query.filter_by(project_id=proj.id).all()
    return task_table_swap_response(proj, tasks)


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
        **task_detail_context(task, proj),
    )


@bp.route("/tasks/<int:task_id>/detail", methods=["POST"])
def update_task_detail(task_id):
    task = Task.query.get_or_404(task_id)
    proj = Project.query.get_or_404(task.project_id)

    errors = []
    new_name = request.form.get("name", task.name).strip()
    if not new_name:
        errors.append("Task name is required.")

    new_description = normalize_rich_text(request.form.get("description", ""))
    new_notes = normalize_rich_text(request.form.get("notes", ""))
    new_links = normalize_rich_text(request.form.get("links", ""))
    new_priority = request.form.get("priority", task.priority or "medium")
    if new_priority not in ("low", "medium", "high"):
        new_priority = task.priority or "medium"

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
    new_start = parse_date(start_raw, calendar=proj.calendar) if start_raw else task.start_date

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
        ctx = task_detail_context(task, proj)
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
        **task_detail_context(task, proj),
    ))
    response.headers["HX-Trigger-After-Swap"] = "refreshStats, refreshDiagram, refreshGrid, refreshRequirements"
    return response
