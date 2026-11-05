"""Project-level routes: index, detail, overview, settings, and Jira export."""

from flask import render_template, request, redirect, url_for, abort, make_response

from ..db import db
from ..models import Project, Task, MOSCOW_CHOICES
from ..services.common import (
    fmt_date,
    lanes_in_order,
    normalize_rich_text,
)
from ..services.scheduler import project_date_stats
from ..jira_export import export_project_csv, export_filename
from .context import bp, task_table_render_kwargs, overview_context
from .raid import raid_context


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


@bp.route("/projects/<int:id>", methods=["GET"])
def project(id):
    proj = Project.query.get_or_404(id)
    lanes = lanes_in_order(proj.id)
    tasks = Task.query.filter_by(project_id=proj.id).all()
    total_effort, total_duration, project_start, project_end = project_date_stats(tasks)
    table_ctx = task_table_render_kwargs(proj, tasks)
    return render_template(
        "project.html",
        project=proj,
        lanes=lanes,
        tasks=tasks,
        task_rows=table_ctx["task_rows"],
        members=table_ctx["members"],
        requirements=table_ctx["requirements"],
        moscow_choices=MOSCOW_CHOICES,
        fmt_date=fmt_date,
        total_effort=total_effort,
        total_duration=total_duration,
        project_start=project_start,
        project_end=project_end,
        **raid_context(proj),
        **overview_context(proj, tasks),
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


@bp.route("/projects/<int:id>/export/jira.csv")
def export_jira_csv(id):
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


@bp.route("/projects/<int:id>/diagram")
def diagram_partial(id):
    proj = Project.query.get_or_404(id)
    lanes = lanes_in_order(proj.id)
    tasks = Task.query.filter_by(project_id=proj.id).all()
    from ..diagram import build_diagram
    return build_diagram(lanes, tasks)
