"""Requirements management routes and partials."""

from flask import render_template, request, redirect, url_for

from ..db import db
from ..models import Project, Requirement, MOSCOW_CHOICES
from ..services.common import (
    requirements_in_order,
    next_requirement_code,
    parse_moscow,
)
from .context import bp


def requirements_partial_context(project):
    return {
        "project": project,
        "requirements": requirements_in_order(project.id),
        "moscow_choices": MOSCOW_CHOICES,
    }


def add_requirement(project, title, description=None, moscow=None, status="open"):
    title = (title or "").strip()
    if not title:
        return None, ("Requirement title is required", 400)
    requirement = Requirement(
        project_id=project.id,
        code=next_requirement_code(project.id),
        title=title,
        description=(description or "").strip() or None,
        moscow=parse_moscow(moscow),
        status=(status or "open").strip(),
    )
    db.session.add(requirement)
    db.session.commit()
    return requirement, None


def update_requirement(project, requirement_id, title, description=None, moscow=None):
    requirement = Requirement.query.filter_by(
        id=requirement_id, project_id=project.id
    ).first_or_404()
    title = (title or "").strip()
    if not title:
        return None, ("Requirement title is required", 400)
    requirement.title = title
    requirement.description = (description or "").strip() or None
    requirement.moscow = parse_moscow(moscow)
    db.session.commit()
    return requirement, None


def delete_requirement(project, requirement_id):
    requirement = Requirement.query.filter_by(
        id=requirement_id, project_id=project.id
    ).first_or_404()
    db.session.delete(requirement)
    db.session.commit()
    return None, None


@bp.route("/projects/<int:id>/requirements", methods=["POST"])
def manage_requirements(id):
    proj = Project.query.get_or_404(id)
    action = request.form.get("action")
    requirement_id = request.form.get("requirement_id", type=int)
    error = None

    if action == "add":
        _, error = add_requirement(
            proj,
            title=request.form.get("title"),
            description=request.form.get("description"),
            moscow=request.form.get("moscow"),
            status=request.form.get("status", "open"),
        )
    elif action == "update":
        _, error = update_requirement(
            proj,
            requirement_id=requirement_id,
            title=request.form.get("title"),
            description=request.form.get("description"),
            moscow=request.form.get("moscow"),
        )
    elif action == "delete":
        _, error = delete_requirement(proj, requirement_id=requirement_id)
    else:
        return "Unknown requirement action", 400

    if error:
        return error

    if request.form.get("partial") == "requirements":
        return render_template(
            "partials/requirements.html",
            **requirements_partial_context(proj),
        )
    return redirect(url_for("main.project", id=proj.id) + "#requirements")


@bp.route("/projects/<int:id>/requirements/list")
def requirements_list_partial(id):
    proj = Project.query.get_or_404(id)
    return render_template(
        "partials/requirements.html",
        **requirements_partial_context(proj),
    )
