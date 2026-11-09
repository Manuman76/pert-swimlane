"""Blueprint factory and shared context for main routes."""

from flask import Blueprint, make_response, render_template

from ..capacity import CAP_STATE_OVER, build_capacity, week_head_label, worst_week
from ..models import Task, Requirement, RaidItem
from ..services.common import (
    fmt_allocation,
    fmt_date,
    lanes_in_order,
    members_in_order,
    requirements_in_order,
)
from ..task_tree import build_task_tree_rows
from ..project_overview import build_project_overview, fmt_overview_day, fmt_variance

bp = Blueprint("main", __name__)


def capacity_overload_map(project, tasks, members, capacity=None):
    """Task id -> the worst week inside that task's own window, when it is over.

    Lets a row warn that the person it is assigned to is already past a full week
    in exactly the weeks the task needs, which is the one capacity fact that fits
    on a compact line. Only a task with an owner *in the team* can be marked: an
    unassigned task has nobody to warn about, and a name outside the team has no
    availability to exceed (the Capacity tab keeps those in a sentence). A done
    task asks nothing of anybody, so it is not marked either.

    One capacity build per grid render, and no build at all when nothing could be
    marked, because this runs on every inline edit. Pass ``capacity`` to reuse the
    model another part of the same page already built — a whole-plan one works, since
    :func:`worst_week` only reads the weeks inside each task's own window.
    """
    dated = [t for t in tasks
             if t.responsible and t.start_date and t.end_date
             and not t.is_milestone and not t.done]
    if not dated or not members:
        return {}
    owners = {t.responsible for t in dated} & {m.name for m in members}
    if not owners:
        return {}
    if capacity is None:
        capacity = build_capacity(project, tasks, members)
    marks = {}
    for task in dated:
        if task.responsible not in owners:
            continue
        worst = worst_week(capacity, task.responsible, task.start_date, task.end_date)
        if worst is None or worst.state != CAP_STATE_OVER:
            continue
        marks[task.id] = {
            "name": task.responsible,
            "percent": worst.percent_str,
            "week": week_head_label(worst.start),
            "text": (f"{task.responsible} is at {worst.percent_str} "
                     f"in the week of {week_head_label(worst.start)}"),
        }
    return marks


def task_table_render_kwargs(project, tasks, capacity=None, **extra):
    """Shared context for the task grid partial (hierarchical row order)."""
    members = members_in_order(project.id)
    return {
        "project": project,
        "lanes": lanes_in_order(project.id),
        "tasks": tasks,
        "task_rows": build_task_tree_rows(tasks),
        "members": members,
        "requirements": requirements_in_order(project.id),
        "capacity_map": capacity_overload_map(project, tasks, members, capacity),
        "fmt_date": fmt_date,
        "fmt_allocation": fmt_allocation,
        **extra,
    }


def task_table_swap_response(proj, tasks, **extra):
    response = make_response(render_template(
        "partials/task_table_body.html",
        **task_table_render_kwargs(proj, tasks, **extra),
    ))
    response.headers["HX-Trigger-After-Swap"] = "refreshStats, refreshDiagram, refreshRequirements, refreshCapacity"
    return response


def dashboard_capacity(project, tasks, members=None):
    """The whole plan in one coarse pass, for the dashboard card and the portfolio cards.

    Deliberately *not* the Capacity tab's window: the tab lets the reader choose
    (8/13/26 weeks or the whole plan) and truncates what does not fit, while a health
    status has to see every week the plan owns — otherwise a project would look calmer
    than it is simply because its last quarter is off-screen. No padding either, since
    these figures are never drawn as columns. Nothing is cached: capacity is derived, and
    a whole-plan pass over a few hundred tasks is single-digit milliseconds.

    Returns ``None`` when the project has no team, which is what tells the Overview card
    to say "no team yet" instead of "nobody is over capacity".
    """
    members = members if members is not None else members_in_order(project.id)
    if not members:
        return None
    return build_capacity(project, tasks, members, min_weeks=0, max_weeks=None)


def overview_context(project, tasks=None, capacity=None):
    if tasks is None:
        tasks = Task.query.filter_by(project_id=project.id).all()
    requirements = requirements_in_order(project.id)
    raid_items = RaidItem.query.filter_by(project_id=project.id).all()
    if capacity is None:
        capacity = dashboard_capacity(project, tasks)
    return {
        "overview": build_project_overview(
            tasks, requirements, raid_items, project=project, capacity=capacity
        ),
        "fmt_overview_day": fmt_overview_day,
        "fmt_variance": fmt_variance,
    }
