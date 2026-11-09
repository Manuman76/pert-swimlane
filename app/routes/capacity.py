"""Capacity routes: per-resource availability.

Absences are the only thing stored here. Whether a week is over capacity is never
saved - :mod:`app.capacity` recomputes it from the tasks, the calendar and these
rows on every request, like `end_date` and `variance_days`.

The Capacity tab itself is P3 of ``PLAN-resource-capacity.md``; the capacity fields
on a member (role, percentage, cost) are saved by ``main.manage_members`` in
:mod:`app.routes.settings`.
"""

from datetime import date, timedelta
from types import SimpleNamespace

from flask import make_response, redirect, render_template, request, url_for

from ..db import db
from ..capacity import CAP_STATE_OVER, build_capacity, find_week_cell, week_start
from ..models import MemberAbsence, Project, Task, TeamMember
from ..services.common import (
    absences_in_order,
    fmt_date,
    members_in_order,
    parse_flexible_date,
)
from .context import bp

# An absence longer than this is a typo (or a career decision), not a plan input.
ABSENCE_MAX_DAYS = 366
# How far ahead Settings looks when it says what someone is carrying: about two
# months, which is the horizon an absence list is usually typed against.
ABSENCE_PREVIEW_WEEKS = 8


def parse_absence_range(start_raw, end_raw=None, default_year=None):
    """(start, end_exclusive, error) from the inclusive pair a person types.

    The form asks for the first day away and, optionally, the last: the two things
    someone knows. Storage keeps the last day + 1, exclusive, the same convention as
    :attr:`Task.end_date`, so a single day off is one day long in both models.
    """
    start = parse_flexible_date(start_raw, default_year=default_year)
    if not start:
        return None, None, ("Absence start date is required (e.g. 2026-07-06)", 400)
    if not (end_raw or "").strip():
        return start, start + timedelta(days=1), None
    last = parse_flexible_date(end_raw, default_year=default_year)
    if not last:
        return None, None, ("Absence end date is not a date (e.g. 2026-07-10)", 400)
    if last < start:
        return None, None, ("The last day away cannot be before the first day", 400)
    days = (last - start).days + 1
    if days > ABSENCE_MAX_DAYS:
        return None, None, (f"An absence can be at most {ABSENCE_MAX_DAYS} days", 400)
    return start, last + timedelta(days=1), None


def add_absence(project, member_id, start_raw, end_raw=None, reason=None):
    member = TeamMember.query.filter_by(id=member_id, project_id=project.id).first()
    if not member:
        return None, ("Choose the team member who is away", 400)

    start, end, error = parse_absence_range(start_raw, end_raw)
    if error:
        return None, error

    clash = MemberAbsence.query.filter_by(
        project_id=project.id, member_id=member.id, start_date=start, end_date=end
    ).first()
    if clash:
        return None, ("That absence is already recorded", 400)

    absence = MemberAbsence(
        project_id=project.id,
        member_id=member.id,
        start_date=start,
        end_date=end,
        reason=(reason or "").strip() or None,
    )
    db.session.add(absence)
    db.session.commit()
    return absence, None


def delete_absence(project, absence_id):
    absence = MemberAbsence.query.filter_by(id=absence_id, project_id=project.id).first_or_404()
    db.session.delete(absence)
    db.session.commit()
    return None, None


def absence_preview(project, weeks=ABSENCE_PREVIEW_WEEKS, today=None):
    """What each person is carrying across the weeks the availability list reaches.

    The window starts at the current week and runs in whole ISO weeks, because a
    week is the unit this app plans in - a headline over half a week would disagree
    with the Capacity tab. The furthest recorded absence extends the window instead
    of being cut, so the warning talks about the dates the list above it shows; an
    absence past ``CAPACITY_MAX_WEEKS`` is listed in ``outside`` instead of checked.

    Returns the weeks covered and a warning for every absence that starts in an
    over-loaded week; ``None`` when the project has nobody on it yet.
    """
    members = members_in_order(project.id)
    if not members:
        return None

    absences = absences_in_order(project.id)
    today = today or date.today()
    first = week_start(today)
    last = first + timedelta(weeks=max(1, weeks) - 1)
    for absence in absences:
        away_last = absence.end_date - timedelta(days=1)
        if week_start(away_last) > last:
            last = week_start(away_last)

    caps = build_capacity(
        project,
        Task.query.filter_by(project_id=project.id).all(),
        members,
        window_start=first,
        window_end=last,
        min_weeks=weeks,
        today=today,
    )
    horizon = caps.weeks[-1].end - timedelta(days=1)
    outside = [absence for absence in absences if absence.start_date > horizon]
    by_id = {row.member_id: row for row in caps.rows if row.member_id}

    # An absence starting in an over-loaded week is the one fact worth interrupting
    # for: that person is being asked to deliver those days as well as be away.
    warnings = {}
    for absence in absences:
        cell = find_week_cell(by_id.get(absence.member_id), absence.start_date)
        if cell is not None and cell.state == CAP_STATE_OVER:
            warnings[absence.id] = cell

    return SimpleNamespace(
        weeks=len(caps.weeks),
        from_date=first,
        to_date=horizon,
        label=f"{fmt_date(first)} - {fmt_date(horizon)}",
        warnings=warnings,
        outside=outside,
    )


@bp.route("/projects/<int:id>/absences", methods=["POST"])
def manage_absences(id):
    """Leave, study and shared-time blocks per person (Settings › Team members).

    Nothing is recalculated: an absence changes who is available in a week, never
    when a task is scheduled to run.
    """
    proj = Project.query.get_or_404(id)
    action = request.form.get("action", "add")
    error = None

    if action == "add":
        _, error = add_absence(
            proj,
            request.form.get("member_id", type=int),
            request.form.get("start_date"),
            request.form.get("end_date"),
            request.form.get("reason"),
        )
    elif action == "delete":
        _, error = delete_absence(proj, request.form.get("absence_id", type=int))
    else:
        return "Unknown absence action", 400

    if error:
        return error

    if request.headers.get("HX-Request") or request.form.get("partial") == "settings":
        return make_response(render_template(
            "partials/settings_absences.html",
            project=proj,
            members=members_in_order(proj.id),
            absences=absences_in_order(proj.id),
            preview=absence_preview(proj),
        ))

    return redirect(url_for("main.project", id=proj.id) + "#settings")
