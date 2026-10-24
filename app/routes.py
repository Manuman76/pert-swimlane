from datetime import datetime, date
from collections import deque

from flask import Blueprint, render_template, request, redirect, url_for, abort, make_response
from .db import db
from .models import Project, Lane, Task, TeamMember, Requirement
from .workdays import count_workdays, next_workday

bp = Blueprint("main", __name__)


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


# ---------------------------------------------------------------------------
# Scheduling
# ---------------------------------------------------------------------------

def recalculate_project(project_id):
    """Recompute start dates for every task in the project.

    Dates follow the 5-day week: durations count working days and no task ever
    starts on a Saturday or Sunday.

    Rule: a task's end date is ``start_date + duration_days`` **working days**
    (the first day a successor may start), and every task must start on or
    after the latest end date of its predecessors. Whenever a task or its
    predecessor set changes, this pushes affected dates forward along the
    dependency chain so the whole project stays consistent; the grid is the
    source of truth and the diagram simply reads the data back.

    * Every start date is first rolled forward to the next working day, so a
      date entered (or inherited) on a weekend lands on the Monday.
    * Tasks with no predecessors keep their entered start date: they anchor
      the schedule.
    * Tasks are processed in dependency order (predecessors first), so a
      change cascades through the full chain in a single pass.
    * Dates only ever move forward: shortening a predecessor never pulls a
      successor earlier.

    Only the start dates of tasks inside this session are touched; the caller
    commits. Returns the codes whose start date was moved.
    """
    by_code = {t.code: t for t in Task.query.filter_by(project_id=project_id)}
    if not by_code:
        return []

    moved = set()

    # Pass 1 - nobody works weekends: roll any Sat/Sun start onto the Monday.
    for code, task in by_code.items():
        workday = next_workday(task.start_date)
        if workday != task.start_date:
            task.start_date = workday
            moved.add(code)

    dependents = {code: [] for code in by_code}
    indegree = {}
    for code, task in by_code.items():
        preds = [p for p in task.predecessor_codes() if p in by_code and p != code]
        indegree[code] = len(preds)
        for p in preds:
            dependents[p].append(code)

    queue = deque(sorted(c for c in by_code if indegree[c] == 0))
    while queue:
        code = queue.popleft()
        task = by_code[code]
        preds = [by_code[p] for p in task.predecessor_codes()
                 if p in by_code and p != code]
        if preds:
            # Every predecessor's end date is already a working day, so the
            # earliest legal start is too.
            earliest = max(p.end_date for p in preds)
            if task.start_date < earliest:
                task.start_date = earliest
                moved.add(code)
        for d in dependents[code]:
            indegree[d] -= 1
            if indegree[d] == 0:
                queue.append(d)
    # Tasks left unprocessed belong to a predecessor cycle; they keep their
    # entered dates (cycles cannot be saved, validate_task rejects them).
    return sorted(moved)


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


def next_requirement_code(project_id):
    count = Requirement.query.filter_by(project_id=project_id).count() + 1
    while Requirement.query.filter_by(project_id=project_id, code=f"BR-{count:03d}").first():
        count += 1
    return f"BR-{count:03d}"


def validate_task(task, project_id, exclude_task_id=None, check_schedule=True):
    errors = []
    if not task.start_date:
        errors.append("Start date is missing or not a valid date.")
    if task.duration_days is None or task.duration_days < 1:
        errors.append("Duration must be an integer >= 1.")

    if task.predecessors:
        all_tasks = Task.query.filter_by(project_id=project_id).all()
        if exclude_task_id:
            all_tasks = [t for t in all_tasks if t.id != exclude_task_id]
        codes = task.predecessor_codes()
        valid_codes = {t.code for t in all_tasks}
        unknown = [c for c in codes if c not in valid_codes]
        if unknown:
            errors.append(f"Unknown predecessor(s): {', '.join(unknown)}")

        task_code = task.code
        if task_code in codes:
            errors.append("A task cannot be its own predecessor.")
        else:
            graph = {t.code: t.predecessor_codes() for t in all_tasks}
            if task_code:
                graph[task_code] = codes

            def has_cycle(start, visited=None, rec_stack=None):
                visited = visited or set()
                rec_stack = rec_stack or set()
                visited.add(start)
                rec_stack.add(start)
                for pred in graph.get(start, []):
                    if pred not in visited:
                        if has_cycle(pred, visited, rec_stack):
                            return True
                    elif pred in rec_stack:
                        return True
                rec_stack.remove(start)
                return False

            for code in list(graph.keys()):
                if has_cycle(code):
                    errors.append("Cycle detected in predecessor dependencies.")
                    break

            # Scheduling violation
            if check_schedule and codes and task.start_date:
                all_tasks_map = {t.code: t for t in all_tasks}
                pred_end_dates = []
                for c in codes:
                    pred_task = all_tasks_map.get(c)
                    if pred_task and pred_task.end_date:
                        pred_end_dates.append(pred_task.end_date)
                if pred_end_dates and task.start_date:
                    max_end = max(pred_end_dates)
                    if task.start_date < max_end:
                        errors.append(f"Task starts before predecessor ends (needs start >= {fmt_date(max_end)}).")
    return errors


# Routes for tasks
# ---------------------------------------------------------------------------

def project_date_stats(tasks):
    """Project-level totals shown above the Lanes panel.

    `total_effort` is the sum of all task durations, and `total_duration` the
    span from the earliest start date to the latest end date - both counted in
    **working days** (Saturday and Sunday excluded), so the numbers match the
    dates on the grid rather than the wall calendar.
    """
    if not tasks:
        return 0, 0, None, None
    total_effort = sum(t.duration_days for t in tasks)
    project_start = min(t.start_date for t in tasks)
    project_end = max(t.end_date for t in tasks)
    return total_effort, count_workdays(project_start, project_end), project_start, project_end


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
            status=request.form.get("status", "open"),
        )
        db.session.add(requirement)
    elif action == "toggle_done":
        requirement = Requirement.query.filter_by(
            id=requirement_id, project_id=proj.id
        ).first_or_404()
        requirement.status = "done" if request.form.get("done") else "open"
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
    else:
        return "Unknown requirement action", 400

    db.session.commit()
    if request.form.get("partial") == "requirements":
        return render_template(
            "partials/requirements.html",
            project=proj,
            requirements=requirements_in_order(proj.id),
        )
    return redirect(url_for("main.project", id=proj.id) + "#requirements")


@bp.route("/projects/<int:id>", methods=["GET"])
def project(id):
    proj = Project.query.get_or_404(id)
    lanes = lanes_in_order(proj.id)
    tasks = Task.query.filter_by(project_id=proj.id).all()
    total_effort, total_duration, project_start, project_end = project_date_stats(tasks)
    return render_template("project.html", project=proj, lanes=lanes, tasks=tasks,
                           members=members_in_order(proj.id),
                           requirements=requirements_in_order(proj.id),
                           fmt_date=fmt_date, total_effort=total_effort,
                           total_duration=total_duration,
                           project_start=project_start, project_end=project_end)


@bp.route("/projects/<int:id>/settings", methods=["POST"])
def update_project_settings(id):
    proj = Project.query.get_or_404(id)
    name = request.form.get("name", "").strip()
    if not name:
        return "Project name is required", 400
    if Project.query.filter(Project.name == name, Project.id != proj.id).first():
        return "A project with that name already exists", 400

    proj.name = name
    proj.description = request.form.get("description", "").strip() or None
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

    task = Task(
        project_id=proj.id,
        code=next_task_code(proj.id),
        lane_id=lane.id,
        name="New Task",
        start_date=next_workday(date.today()),
        duration_days=1,
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
    response = make_response(render_template("partials/task_table_body.html", project=proj, lanes=lanes, tasks=tasks, members=members_in_order(proj.id), requirements=requirements_in_order(proj.id), fmt_date=fmt_date))
    response.headers["HX-Trigger-After-Swap"] = "refreshStats, refreshDiagram"
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
    if dur_raw:
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
    new_preds = request.form.get("predecessors", task.predecessors or "").strip() or None
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
            project=proj,
            lanes=lanes,
            tasks=tasks,
            members=members_in_order(proj.id),
            requirements=requirements_in_order(proj.id),
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
            fmt_date=fmt_date,
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
    response = make_response(render_template("partials/task_table_body.html", project=proj, lanes=lanes, tasks=tasks, members=members_in_order(proj.id), requirements=requirements_in_order(proj.id), fmt_date=fmt_date))
    response.headers["HX-Trigger-After-Swap"] = "refreshStats, refreshDiagram"
    return response


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
    response = make_response(render_template("partials/task_table_body.html", project=proj, lanes=lanes, tasks=tasks, members=members_in_order(proj.id), requirements=requirements_in_order(proj.id), fmt_date=fmt_date))
    response.headers["HX-Trigger-After-Swap"] = "refreshStats, refreshDiagram"
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


# Diagram partials
# ---------------------------------------------------------------------------

@bp.route("/projects/<int:id>/diagram")
def diagram_partial(id):
    proj = Project.query.get_or_404(id)
    lanes = lanes_in_order(proj.id)
    tasks = Task.query.filter_by(project_id=proj.id).all()
    from .diagram import build_diagram
    return build_diagram(lanes, tasks)
