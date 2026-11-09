"""Capacity routes: per-resource availability and the Capacity tab.

Absences are the only thing stored here. Whether a week is over capacity is never
saved - :mod:`app.capacity` recomputes it from the tasks, the calendar and these
rows on every request, like `end_date` and `variance_days`.

The capacity fields on a member (role, percentage, cost) are saved by
``main.manage_members`` in :mod:`app.routes.settings`.
"""

import csv
import io
from datetime import date, timedelta
from types import SimpleNamespace
from urllib.parse import urlencode

from flask import make_response, redirect, render_template, request, url_for

from ..db import db
from ..capacity import (
    CAPACITY_MAX_WEEKS,
    CAP_STATE_OVER,
    build_capacity,
    find_week_cell,
    fmt_capacity_days,
    week_start,
)
from ..jira_export import export_stem
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
# The Capacity tab's window picker. "Whole project" is not a choice here: it is
# what happens when the toolbar is left on the widest option and the plan is short.
CAPACITY_WEEK_CHOICES = (8, 13, 26)
CAPACITY_MODE_REMAINING = "remaining"
CAPACITY_MODE_FULL = "full"
CAPACITY_MODE_CHOICES = (
    (CAPACITY_MODE_REMAINING, "Remaining effort"),
    (CAPACITY_MODE_FULL, "Full plan"),
)


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
        response = make_response(render_template(
            "partials/settings_absences.html",
            project=proj,
            members=members_in_order(proj.id),
            absences=absences_in_order(proj.id),
            preview=absence_preview(proj),
        ))
        # The week the absence lands in is a different week now, and so is every
        # headline figure that reads it.
        response.headers["HX-Trigger-After-Swap"] = "refreshStats, refreshDiagram, refreshCapacity"
        return response

    return redirect(url_for("main.project", id=proj.id) + "#settings")


# --------------------------------------------------------------------------- #
# The Capacity tab
# --------------------------------------------------------------------------- #

def parse_capacity_weeks(raw):
    """Column count from the toolbar; blank means the plan's own window, which is
    what a reader means by "whole project".

    Anything that is not a number is treated as that same "show me the plan", so the
    picker's ``whole`` and a hand-typed ``whole-project`` agree. A number is honoured
    up to the engine's horizon rather than snapped to the picker: typing 12 into a
    select that offers 8 is not a mistake.
    """
    text = (raw or "").strip().lower()
    if not text:
        return None
    try:
        value = int(text)
    except (TypeError, ValueError):
        return None
    return max(1, min(value, CAPACITY_MAX_WEEKS))


def parse_capacity_mode(raw):
    """``full`` for the full plan; anything else asks for the effort still to come."""
    return CAPACITY_MODE_FULL if (raw or "").strip().lower() == CAPACITY_MODE_FULL else CAPACITY_MODE_REMAINING


def month_bands(columns):
    """Consecutive weeks under one month, as ``[("Jun", 4), ("Jul", 4)]`` for the header."""
    bands = []
    for column in columns:
        label = column.month_label
        if bands and bands[-1][0] == label:
            bands[-1] = (label, bands[-1][1] + 1)
        else:
            bands.append((label, 1))
    return bands


def capacity_query_args(*, start=None, weeks=None, mode=None):
    """Query string for the toolbar and the CSV link, so Export exports the window on
    the screen and not a different one. Defaults are left out to keep links short."""
    params = {}
    if start:
        params["start"] = start.isoformat()
    if weeks:
        params["weeks"] = weeks
    if parse_capacity_mode(mode) != CAPACITY_MODE_REMAINING:
        params["mode"] = parse_capacity_mode(mode)
    return urlencode(params)


def capacity_request_args(args=None):
    """Toolbar state from a query string (the tab is a GET, so Back works)."""
    args = request.args if args is None else args
    return {
        "start": parse_flexible_date(args.get("start")),
        "weeks": parse_capacity_weeks(args.get("weeks")),
        "mode": parse_capacity_mode(args.get("mode")),
    }


def capacity_context(project, tasks=None, *, start=None, weeks=None, mode=None, today=None):
    """Grid, columns and toolbar state for the Capacity tab.

    The window is chosen by the reader: an explicit number of weeks, an explicit
    starting week, or the plan's own span. Nothing here is stored, so moving a task or
    booking an absence changes the next render and nothing else.
    """
    if tasks is None:
        tasks = Task.query.filter_by(project_id=project.id).all()
    mode = parse_capacity_mode(mode)
    caps = build_capacity(
        project,
        list(tasks),
        members_in_order(project.id),
        window_start=start,
        weeks=weeks,
        remaining_effort=(mode != CAPACITY_MODE_FULL),
        today=today or date.today(),
    )
    return {
        "capacity": caps,
        "capacity_start": start,
        "capacity_weeks": weeks,
        "capacity_mode": mode,
        "capacity_mode_choices": CAPACITY_MODE_CHOICES,
        "capacity_week_choices": CAPACITY_WEEK_CHOICES,
        "capacity_months": month_bands(caps.weeks),
        # Named weeks, not a date range: the second date is where the last column
        # starts, so a one-week window says one week and does not pretend to span two.
        "capacity_window_note": (
            "1 week from {}".format(fmt_date(caps.window_start))
            if caps.window_weeks == 1
            else "{} weeks from {} to {}".format(
                caps.window_weeks, fmt_date(caps.window_start), fmt_date(caps.weeks[-1].start)
            )
        ),
        "capacity_query": capacity_query_args(start=start, weeks=weeks, mode=mode),
        "fmt_capacity_days": fmt_capacity_days,
        "fmt_date": fmt_date,
    }


def capacity_csv(capacity):
    """The grid as one row per person per week, plus the team's weekly total.

    Long rather than wide: 26 weeks times four numbers is a hundred columns nobody
    reads, and a spreadsheet can pivot this back into a grid in one drag.
    """
    rows = list(capacity.rows)
    if capacity.unassigned_row is not None:
        rows.append(capacity.unassigned_row)

    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow([
        "Member", "Row type", "Role", "Capacity %", "Week", "Week starting",
        "Booked days", "Available days", "Utilization %", "Over days", "Tasks",
    ])
    for row in rows:
        for cell in row.weeks:
            writer.writerow([
                row.name,
                row.kind,
                row.role,
                row.capacity_percent,
                capacity.weeks[cell.index].full_label,
                cell.start.isoformat(),
                cell.demand,
                cell.supply,
                cell.percent,        # None stays blank: a week nobody can work has no ratio
                cell.over_days,
                " ".join(cell.task_codes),
            ])
    for total in capacity.totals:
        writer.writerow([
            "Team total",
            "team",
            "",
            "",
            total.label,
            total.start.isoformat(),
            total.demand,
            total.supply,
            total.percent,
            "",
            "",
        ])
    return out.getvalue()


@bp.route("/projects/<int:id>/capacity", methods=["GET"])
def capacity_partial(id):
    """The Capacity tab: a week-by-week grid of who is booked to do what."""
    proj = Project.query.get_or_404(id)
    return render_template(
        "partials/capacity.html",
        project=proj,
        **capacity_context(proj, **capacity_request_args()),
    )


@bp.route("/projects/<int:id>/capacity/export.csv", methods=["GET"])
def capacity_export_csv(id):
    """Same window as the tab, as a spreadsheet-sized answer."""
    proj = Project.query.get_or_404(id)
    args = capacity_request_args()
    caps = capacity_context(proj, **args)["capacity"]
    response = make_response(capacity_csv(caps))
    response.headers["Content-Type"] = "text/csv; charset=utf-8"
    response.headers["Content-Disposition"] = (
        f'attachment; filename="{export_stem(proj)}-capacity.csv"'
    )
    return response
