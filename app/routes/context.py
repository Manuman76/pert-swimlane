"""Blueprint factory and shared context for main routes."""

from flask import Blueprint, make_response, render_template

from ..models import Task, Requirement, RaidItem
from ..services.common import (
    fmt_date,
    lanes_in_order,
    members_in_order,
    requirements_in_order,
)
from ..task_tree import build_task_tree_rows
from ..project_overview import build_project_overview, fmt_overview_day, fmt_variance

bp = Blueprint("main", __name__)


def task_table_render_kwargs(project, tasks, **extra):
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


def task_table_swap_response(proj, tasks, **extra):
    response = make_response(render_template(
        "partials/task_table_body.html",
        **task_table_render_kwargs(proj, tasks, **extra),
    ))
    response.headers["HX-Trigger-After-Swap"] = "refreshStats, refreshDiagram, refreshRequirements"
    return response


def overview_context(project, tasks=None):
    if tasks is None:
        tasks = Task.query.filter_by(project_id=project.id).all()
    requirements = requirements_in_order(project.id)
    raid_items = RaidItem.query.filter_by(project_id=project.id).all()
    return {
        "overview": build_project_overview(tasks, requirements, raid_items, project=project),
        "fmt_overview_day": fmt_overview_day,
        "fmt_variance": fmt_variance,
    }
