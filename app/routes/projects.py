"""Project-level routes: index, detail, overview, settings, and Jira export."""

from flask import render_template, request, redirect, url_for, abort, make_response

from ..db import db
from ..models import Project, Task, MOSCOW_CHOICES
from ..services.common import (
    absences_in_order,
    fmt_allocation,
    fmt_date,
    lanes_in_order,
    member_roles_in_use,
    normalize_rich_text,
)
from ..services.scheduler import (
    project_date_stats,
    set_project_baseline,
    clear_project_baseline,
)
from ..jira_export import export_project_csv, export_filename
from ..project_overview import build_project_overview, fmt_overview_day
from .context import bp, task_table_render_kwargs, overview_context, dashboard_capacity
from .capacity import absence_preview
from .raid import raid_context
from .settings import WEEKDAY_CHOICES


def get_project_stats(project):
    tasks = project.tasks
    requirements = project.requirements
    raid_items = project.raid_items
    # One coarse capacity pass per project, reusing the relationship loads the card
    # already pays for: the badge and the portfolio KPI read the same figures the
    # project's own Overview card will show.
    overview = build_project_overview(
        tasks, requirements, raid_items, project=project,
        capacity=dashboard_capacity(project, tasks, members=project.members),
    )
    return {
        "id": project.id,
        "name": project.name,
        "description": project.description,
        "updated_at": project.updated_at,
        "task_count": len(tasks),
        "lanes_count": len(project.lanes),
        "members_count": len(project.members),
        "overview": overview,
    }


@bp.route("/")
def index():
    projects = Project.query.order_by(Project.updated_at.desc()).all()
    project_stats = [get_project_stats(p) for p in projects]

    total_projects = len(project_stats)
    on_track_count = sum(1 for p in project_stats if p["overview"].health_status in ("on_track", "completed"))
    at_risk_count = sum(1 for p in project_stats if p["overview"].health_status == "at_risk")
    critical_count = sum(1 for p in project_stats if p["overview"].health_status == "critical")

    total_tasks = sum(p["overview"].tasks_total for p in project_stats)
    total_completed = sum(p["overview"].tasks_completed for p in project_stats)
    portfolio_progress = round(100 * total_completed / total_tasks) if total_tasks else 0

    total_milestones = sum(p["overview"].milestones_total for p in project_stats)
    milestones_reached = sum(p["overview"].milestones_completed for p in project_stats)

    # People are counted per project, not merged across them: the same name in two
    # projects is two bookings against that name, so a total of "3 over capacity" means
    # three project-people, not three humans. Stated in the card's title attribute.
    over_allocated_count = sum(p["overview"].members_over for p in project_stats)
    projects_over_capacity = sum(1 for p in project_stats if p["overview"].members_over)

    portfolio_metrics = {
        "total_projects": total_projects,
        "on_track_count": on_track_count,
        "at_risk_count": at_risk_count,
        "critical_count": critical_count,
        "over_allocated_count": over_allocated_count,
        "projects_over_capacity": projects_over_capacity,
        "total_tasks": total_tasks,
        "total_completed": total_completed,
        "portfolio_progress": portfolio_progress,
        "total_milestones": total_milestones,
        "milestones_reached": milestones_reached,
    }

    return render_template(
        "index.html",
        projects=project_stats,
        portfolio=portfolio_metrics,
        fmt_overview_day=fmt_overview_day,
    )


@bp.route("/projects", methods=["POST"])
def create_project():
    name = request.form.get("name", "").strip()
    description = normalize_rich_text(request.form.get("description", ""))
    if not name:
        return "Project name is required", 400
    if Project.query.filter(Project.name == name).first():
        return "A project with that name already exists", 400
    proj = Project(name=name, description=description)
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


@bp.route("/projects/<int:id>", methods=["GET"])
def project(id):
    proj = Project.query.get_or_404(id)
    lanes = lanes_in_order(proj.id)
    tasks = Task.query.filter_by(project_id=proj.id).all()
    total_effort, total_duration, project_start, project_end = project_date_stats(tasks)
    # One whole-plan capacity model for the whole page: the grid's overload marks and
    # the Overview's Resources card read the same build instead of paying for two.
    capacity = dashboard_capacity(proj, tasks)
    table_ctx = task_table_render_kwargs(proj, tasks, capacity=capacity)
    return render_template(
        "project.html",
        project=proj,
        lanes=lanes,
        tasks=tasks,
        task_rows=table_ctx["task_rows"],
        members=table_ctx["members"],
        member_roles=member_roles_in_use(proj.id),
        absences=absences_in_order(proj.id),
        preview=absence_preview(proj),
        requirements=table_ctx["requirements"],
        moscow_choices=MOSCOW_CHOICES,
        capacity_map=table_ctx["capacity_map"],
        fmt_date=fmt_date,
        fmt_allocation=fmt_allocation,
        total_effort=total_effort,
        total_duration=total_duration,
        project_start=project_start,
        project_end=project_end,
        weekday_choices=WEEKDAY_CHOICES,
        **raid_context(proj),
        **overview_context(proj, tasks, capacity=capacity),
    )


@bp.route("/projects/<int:id>/overview")
def project_overview_partial(id):
    proj = Project.query.get_or_404(id)
    tasks = Task.query.filter_by(project_id=proj.id).all()
    return render_template(
        "partials/project_overview.html",
        project=proj,
        **overview_context(proj, tasks),
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
    proj.description = normalize_rich_text(request.form.get("description", ""))
    db.session.commit()
    return redirect(url_for("main.project", id=proj.id) + "#settings")


@bp.route("/projects/<int:id>/baseline", methods=["POST"])
def manage_baseline(id):
    proj = Project.query.get_or_404(id)
    action = request.form.get("action", "set")
    if action == "clear":
        clear_project_baseline(proj.id)
    else:
        set_project_baseline(proj.id)

    tasks = Task.query.filter_by(project_id=proj.id).all()
    if request.headers.get("HX-Request") or request.form.get("partial") == "overview":
        response = make_response(render_template(
            "partials/project_overview.html",
            project=proj,
            **overview_context(proj, tasks),
        ))
        response.headers["HX-Trigger-After-Swap"] = "refreshStats, refreshDiagram, refreshGrid, refreshCapacity"
        return response

    tab_fragment = "#settings" if request.form.get("tab") == "settings" else "#overview"
    return redirect(url_for("main.project", id=proj.id) + tab_fragment)


@bp.route("/projects/<int:id>/export/jira.csv")
def export_jira_csv(id):
    proj = Project.query.get_or_404(id)
    lanes = lanes_in_order(proj.id)
    tasks = Task.query.filter_by(project_id=proj.id).all()
    csv_text = export_project_csv(proj, lanes, tasks, members=proj.members)
    response = make_response(csv_text)
    response.headers["Content-Type"] = "text/csv; charset=utf-8"
    response.headers["Content-Disposition"] = (
        f'attachment; filename="{export_filename(proj)}"'
    )
    return response


@bp.route("/projects/<int:id>/diagram")
def diagram_partial(id):
    proj = Project.query.get_or_404(id)
    lanes = lanes_in_order(proj.id)
    tasks = Task.query.filter_by(project_id=proj.id).all()
    collapsed_raw = request.args.get("collapsed", "")
    collapsed_lanes = set()
    if collapsed_raw:
        for val in collapsed_raw.split(","):
            try:
                collapsed_lanes.add(int(val.strip()))
            except (ValueError, TypeError):
                pass
    from ..diagram import build_diagram
    return build_diagram(lanes, tasks, members=proj.members, collapsed_lanes=collapsed_lanes)
