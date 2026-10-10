from datetime import datetime, date
from flask import Blueprint, render_template, request, redirect, url_for, abort
from .db import db
from .models import Project, Lane, Task

bp = Blueprint("main", __name__)


def fmt_date(d):
    return d.strftime("%d %b %Y") if d else ""


def parse_date(s):
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%d %b %Y", "%d %B %Y"):
        try:
            return datetime.strptime(s.strip(), fmt).date()
        except ValueError:
            continue
    return None


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


# Lanes
@bp.route("/projects/<int:id>/lanes", methods=["POST"])
def manage_lanes(id):
    proj = Project.query.get_or_404(id)
    action = request.form.get("action")

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
            lane.sort_order, above.sort_order = above.sort_order, lane.sort_order
            db.session.commit()
    elif action == "move_down":
        lane_id = request.form.get("lane_id", type=int)
        lane = Lane.query.filter_by(id=lane_id, project_id=proj.id).first_or_404()
        below = Lane.query.filter(Lane.project_id == proj.id, Lane.sort_order > lane.sort_order).order_by(Lane.sort_order).first()
        if below:
            lane.sort_order, below.sort_order = below.sort_order, lane.sort_order
            db.session.commit()
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

    return redirect(url_for("main.project", id=proj.id))


# Tasks helpers
# ---------------------------------------------------------------------------

def next_task_code(project_id):
    max_code = db.session.query(db.func.max(Task.code)).filter_by(project_id=project_id).scalar()
    if max_code:
        next_num = int(max_code) + 1
    else:
        next_num = 1
    return f"{next_num:03d}"


def validate_task(task, project_id, exclude_task_id=None):
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
            if codes and task.start_date:
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

@bp.route("/projects/<int:id>", methods=["GET"])
def project(id):
    proj = Project.query.get_or_404(id)
    lanes = Lane.query.filter_by(project_id=proj.id).order_by(Lane.sort_order).all()
    tasks = Task.query.filter_by(project_id=proj.id).all()
    return render_template("project.html", project=proj, lanes=lanes, tasks=tasks, fmt_date=fmt_date)


@bp.route("/projects/<int:id>/tasks", methods=["POST"])
def create_task(id):
    proj = Project.query.get_or_404(id)
    lanes = Lane.query.filter_by(project_id=proj.id).order_by(Lane.sort_order).all()
    if not lanes:
        return "No lanes available. Create a lane first.", 400

    lane_id = request.form.get("lane_id", type=int)
    if not lane_id:
        lane_id = lanes[0].id

    task = Task(
        project_id=proj.id,
        code=next_task_code(proj.id),
        lane_id=lane_id,
        name="New Task",
        start_date=date.today(),
        duration_days=1,
        responsible=None,
        predecessors=None,
    )
    db.session.add(task)
    db.session.commit()
    tasks = Task.query.filter_by(project_id=proj.id).all()
    return render_template("partials/task_table_body.html", project=proj, lanes=lanes, tasks=tasks, fmt_date=fmt_date)


@bp.route("/tasks/<int:task_id>/update", methods=["POST"])
def update_task(task_id):
    task = Task.query.get_or_404(task_id)
    proj = Project.query.get_or_404(task.project_id)
    lanes = Lane.query.filter_by(project_id=proj.id).order_by(Lane.sort_order).all()

    # Gather new values
    new_name = request.form.get("name", task.name).strip()
    new_lane_id = request.form.get("lane_id", type=int)
    if not new_lane_id:
        new_lane_id = task.lane_id

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

    new_resp = request.form.get("responsible", task.responsible or "").strip() or None
    new_preds = request.form.get("predecessors", task.predecessors or "").strip() or None

    # Validate using a temporary task without touching the real one
    temp = Task(
        project_id=proj.id,
        code=task.code,
        lane_id=new_lane_id,
        name=new_name,
        start_date=new_start,
        duration_days=new_dur,
        responsible=new_resp,
        predecessors=new_preds,
    )
    errors = validate_task(temp, proj.id, exclude_task_id=task.id)

    if errors:
        tasks = Task.query.filter_by(project_id=proj.id).all()
        return render_template(
            "partials/task_table_body.html",
            project=proj,
            lanes=lanes,
            tasks=tasks,
            errors={task.id: errors},
            drafts={
                task.id: {
                    "name": new_name,
                    "lane_id": new_lane_id,
                    "start_date": new_start if new_start else None,
                    "duration_days": new_dur if new_dur >= 0 else None,
                    "responsible": new_resp,
                    "predecessors": new_preds,
                }
            },
            fmt_date=fmt_date,
        ), 422

    task.name = new_name
    task.lane_id = new_lane_id
    task.start_date = new_start
    task.duration_days = new_dur
    task.responsible = new_resp
    task.predecessors = new_preds
    db.session.commit()
    tasks = Task.query.filter_by(project_id=proj.id).all()
    return render_template("partials/task_table_body.html", project=proj, lanes=lanes, tasks=tasks, fmt_date=fmt_date)


@bp.route("/tasks/<int:task_id>/delete", methods=["POST"])
def delete_task(task_id):
    task = Task.query.get_or_404(task_id)
    proj = Project.query.get_or_404(task.project_id)
    lanes = Lane.query.filter_by(project_id=proj.id).order_by(Lane.sort_order).all()

    # Remove references to this task from other tasks' predecessors
    code = task.code
    all_tasks = Task.query.filter_by(project_id=proj.id).all()
    for t in all_tasks:
        if t.predecessors and code in t.predecessor_codes():
            codes = [c for c in t.predecessor_codes() if c != code]
            t.predecessors = ", ".join(codes) if codes else None

    db.session.delete(task)
    db.session.commit()
    tasks = Task.query.filter_by(project_id=proj.id).all()
    return render_template("partials/task_table_body.html", project=proj, lanes=lanes, tasks=tasks, fmt_date=fmt_date)


# Diagram partials
# ---------------------------------------------------------------------------

@bp.route("/projects/<int:id>/diagram")
def diagram_partial(id):
    proj = Project.query.get_or_404(id)
    lanes = Lane.query.filter_by(project_id=proj.id).order_by(Lane.sort_order).all()
    tasks = Task.query.filter_by(project_id=proj.id).all()
    from .diagram import build_diagram
    return build_diagram(lanes, tasks)
