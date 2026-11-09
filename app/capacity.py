"""Resource capacity: person-day supply and demand, bucketed per ISO week.

Capacity is **derived, never stored** — like :attr:`Task.end_date`,
:data:`Task.variance_days` and requirement coverage, it is computed from the grid on
every read, so there is nothing to keep in sync when a date, a duration or an
assignee changes.

The unit is the **person-day**, not the hour. The whole application counts working
days (:mod:`app.workdays`, ``Task.duration_days``), so capacity uses the same ruler:

* **supply** — working days in the week for the *project calendar* (its working-day
  pattern and its holidays), minus that member's absences, times the member's
  ``capacity_percent``;
* **demand** — the working days a task occupies in that week, times the task's
  ``allocation_percent`` (how much of a person's day the task consumes while it runs),
  times the effort still to do.

Two consequences worth stating out loud:

* **Effort is not duration.** Two people at 50% on a 5-day task is 5 person-days of
  work spread over one calendar week. ``duration_days`` stays elapsed working days.
* **Weeks come from ISO Mondays**, so a 6-month project is ~26 columns and a bucket
  boundary never falls mid-week in a way that loses a day: a day is counted in exactly
  the one week it belongs to, and never twice.

This module is deliberately free of Flask and never writes to the session: pass a
project (or ``None`` for a plain Mon-Fri, holiday-free calendar), the tasks and the
members, and read the returned :class:`~types.SimpleNamespace`.
"""

from datetime import date, timedelta
from types import SimpleNamespace

from .workdays import DEFAULT_CALENDAR, add_workdays, count_workdays

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

#: Only weekly buckets today; ``"month"`` is the planned follow-up.
BUCKET_WEEKS = "week"

#: Utilization (demand / supply) bands, as a fraction of a member's weekly supply.
CAPACITY_THRESHOLD_IDLE = 0.5
CAPACITY_THRESHOLD_TIGHT = 0.9
CAPACITY_THRESHOLD_OVER = 1.0

#: Rendering bounds for a planning window. Below :data:`CAPACITY_MIN_WEEKS` a window is
#: padded (an empty two-week tab tells you nothing); above
#: :data:`CAPACITY_MAX_WEEKS` it is truncated and ``window_truncated`` is set so the
#: template can say "showing 26 of 31 weeks".
CAPACITY_MIN_WEEKS = 8
CAPACITY_MAX_WEEKS = 26

#: Cell states, consumed as CSS class suffixes (``cap-idle``, ``cap-over``, …).
CAP_STATE_NONE = "none"
CAP_STATE_IDLE = "idle"
CAP_STATE_NORMAL = "normal"
CAP_STATE_TIGHT = "tight"
CAP_STATE_OVER = "over"
CAPACITY_STATES = (
    CAP_STATE_NONE,
    CAP_STATE_IDLE,
    CAP_STATE_NORMAL,
    CAP_STATE_TIGHT,
    CAP_STATE_OVER,
)

ROW_MEMBER = "member"
ROW_UNASSIGNED = "unassigned"
UNASSIGNED_LABEL = "Unassigned"

MODE_REMAINING = "remaining"
MODE_FULL = "full"

_EPS = 1e-9


# ---------------------------------------------------------------------------
# Week helpers
# ---------------------------------------------------------------------------

def week_start(d):
    """Monday of the ISO week containing ``d`` (``d`` is returned untouched if Monday)."""
    if not d:
        return None
    return d - timedelta(days=d.weekday())


def week_ends(d):
    """The exclusive end of the week containing ``d`` (the following Monday)."""
    start = week_start(d)
    return start + timedelta(weeks=1) if start else None


def week_label(monday):
    """Short column header, e.g. ``W23``."""
    return f"W{_iso_week(monday)}"


def week_full_label(monday):
    """Sortable, unambiguous label, e.g. ``2026-W23``."""
    return f"{_iso_year(monday)}-W{_iso_week(monday):02d}"


def week_head_label(monday):
    """Compact date under the week number, e.g. ``8 Jun``."""
    text = monday.strftime("%d %b")
    return text[1:] if text.startswith("0") else text


def month_label(monday):
    """Month header band label for a week, e.g. ``Jun 2026`` (the month of the Monday)."""
    return monday.strftime("%b %Y")


def iter_week_mondays(start, count):
    return [start + timedelta(weeks=i) for i in range(max(0, int(count)))]


def _iso_week(monday):
    return monday.isocalendar()[1]


def _iso_year(monday):
    return monday.isocalendar()[0]


def fmt_capacity_days(value):
    """Format person-days for display: ``2d``, ``1.5d``, ``0d``."""
    if value is None:
        return ""
    value = round(float(value), 1)
    if value == int(value):
        return f"{int(value)}d"
    return f"{value:.1f}d"


def fmt_capacity_percent(percent):
    """Format utilization: ``87%``, or ``—`` when there is no supply to measure."""
    if percent is None:
        return "—"
    return f"{int(percent)}%"


# ---------------------------------------------------------------------------
# State / thresholds
# ---------------------------------------------------------------------------

def capacity_state(demand, supply):
    """Band for one cell: ``none`` | ``idle`` | ``normal`` | ``tight`` | ``over``.

    ``supply`` is what the person can actually work that week. Zero supply with no
    demand is ``none`` (a holiday week, or a member on 0%% capacity); zero supply *with*
    demand is ``over`` — work with nowhere to go.
    """
    demand = float(demand or 0.0)
    supply = float(supply or 0.0)
    if supply <= _EPS:
        return CAP_STATE_OVER if demand > _EPS else CAP_STATE_NONE
    ratio = demand / supply
    if ratio > CAPACITY_THRESHOLD_OVER + _EPS:
        return CAP_STATE_OVER
    if ratio >= CAPACITY_THRESHOLD_TIGHT:
        return CAP_STATE_TIGHT
    if ratio >= CAPACITY_THRESHOLD_IDLE:
        return CAP_STATE_NORMAL
    return CAP_STATE_IDLE


def capacity_percent(demand, supply):
    """Utilization as a whole percent, or ``None`` when supply is zero."""
    supply = float(supply or 0.0)
    if supply <= _EPS:
        return None
    return int(round(100.0 * float(demand or 0.0) / supply))


# ---------------------------------------------------------------------------
# Task / member field readers (defensive so plain objects work in tests)
# ---------------------------------------------------------------------------

def _calendar_for(project):
    return getattr(project, "calendar", None) or DEFAULT_CALENDAR


def _norm_name(value):
    return (value or "").strip().lower()


def _capacity_fraction(member):
    """Member availability as a fraction of one full-time person (``None`` → 100%%)."""
    raw = getattr(member, "capacity_percent", None)
    if raw is None:
        return 1.0
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return 1.0
    return max(0.0, min(value, 400.0)) / 100.0


def _allocation_fraction(task):
    """Share of one person's day the task consumes (``None`` → 100%%, clamped 0–100%%)."""
    raw = getattr(task, "allocation_percent", None)
    if raw is None:
        return 1.0
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return 1.0
    return max(0.0, min(value, 100.0)) / 100.0


def _remaining_factor(task):
    """Effort still to do as a fraction of the whole task (not started → 1.0, done → 0.0).

    ``display_progress`` wins when present; otherwise the status flags give the same
    100/50/0 reading the progress bar uses.
    """
    progress = getattr(task, "display_progress", None)
    if progress is None:
        if getattr(task, "done", False):
            return 0.0
        progress = 50.0 if getattr(task, "started", False) else 0.0
    try:
        progress = float(progress)
    except (TypeError, ValueError):
        progress = 0.0
    return 1.0 - max(0.0, min(progress, 100.0)) / 100.0


def _task_code(task):
    return getattr(task, "code", "") or ""


def _task_span(task, cal):
    """``(start, end_exclusive)`` of the days a task occupies, or ``None``.

    Milestones are points in time, so they get a one-day footprint for the *window*
    (the delivery gate should be inside the chart) while contributing no demand.
    Dates are recomputed from the calendar we are building against rather than from
    :attr:`Task.end_date`, which resolves the calendar through the ``project``
    backref and would silently fall back to Mon-Fri for an unattached task.
    """
    start = getattr(task, "start_date", None)
    if not start:
        return None
    if getattr(task, "is_milestone", False):
        return (start, start + timedelta(days=1))
    duration = int(getattr(task, "duration_days", 0) or 0)
    if duration <= 0:
        return None
    end = add_workdays(start, duration, calendar=cal)
    if not end or end <= start:
        return None
    return (start, end)


def _member_absences(member):
    for absence in (getattr(member, "absences", None) or []):
        if getattr(absence, "start_date", None) and getattr(absence, "end_date", None):
            yield absence


def _absence_days(cal, member, monday, week_end):
    """Working days the member is away inside ``[monday, week_end)``.

    Absence ranges are ``[start_date, end_date)`` like ``Task.end_date``. A day set
    keeps overlapping ranges and a range spanning the whole week from being counted
    twice; asking for a Saturday never consumes capacity either.
    """
    away = set()
    for absence in _member_absences(member):
        first = max(absence.start_date, monday)
        last = min(absence.end_date, week_end)
        day = first
        while day < last:
            if cal.is_workday(day):
                away.add(day)
            day += timedelta(days=1)
    return len(away)


# ---------------------------------------------------------------------------
# Window
# ---------------------------------------------------------------------------

def _planning_window(spans, cal, window_start, window_end, weeks, min_weeks, max_weeks, today):
    """Return ``(mondays, truncated)`` for the weeks to chart.

    The window is grown to whole ISO weeks on both sides, so every working day of every
    task lands in exactly one column. A window the caller bounded with ``window_end`` is
    never padded: padding exists so a two-week project still shows enough horizon to be
    worth reading, and that argument does not apply when someone asked for three weeks.
    """
    if weeks is not None:
        count = max(1, int(weeks))
        first = week_start(window_start) if window_start else None
        if first is None:
            first = _earliest_start(spans) or week_start(today or date.today())
        truncated = bool(max_weeks and count > max_weeks)
        if truncated:
            count = max_weeks
        return iter_week_mondays(first, count), truncated

    starts = [s for s, _ in spans]
    ends = [e for _, e in spans]

    if window_start:
        first = week_start(window_start)
    elif starts:
        first = week_start(min(starts))
    else:
        first = week_start(today or date.today())

    if window_end:
        last_exclusive = week_ends(window_end)
    elif ends:
        last_exclusive = week_ends(max(ends) - timedelta(days=1))
    else:
        last_exclusive = first + timedelta(weeks=1)
    if last_exclusive <= first:
        last_exclusive = first + timedelta(weeks=1)

    count = (last_exclusive - first).days // 7
    if min_weeks and window_end is None and count < min_weeks:
        count = min_weeks
    truncated = False
    if max_weeks and count > max_weeks:
        count = max_weeks
        truncated = True
    return iter_week_mondays(first, count), truncated


def _earliest_start(spans):
    starts = [s for s, _ in spans]
    return week_start(min(starts)) if starts else None


# ---------------------------------------------------------------------------
# Result rows
# ---------------------------------------------------------------------------

def _new_cell(index):
    return {
        "index": index,
        "demand": 0.0,
        "supply": 0.0,
        "codes": [],
    }


def _cell_view(cell, monday, week_end):
    demand = round(cell["demand"], 4)
    supply = round(cell["supply"], 4)
    percent = capacity_percent(demand, supply)
    return SimpleNamespace(
        index=cell["index"],
        start=monday,
        end=week_end,
        demand=demand,
        supply=supply,
        demand_str=fmt_capacity_days(demand),
        supply_str=fmt_capacity_days(supply),
        percent=percent,
        percent_str=fmt_capacity_percent(percent),
        state=capacity_state(demand, supply),
        over_days=round(max(0.0, demand - supply), 4),
        free_days=round(max(0.0, supply - demand), 4),
        task_codes=list(cell["codes"]),
    )


def _row_from_cells(row_info, cells, week_mondays):
    """Attach the per-row aggregates the tab and the KPI cards read."""
    views = [
        _cell_view(cell, week_mondays[cell["index"]], week_mondays[cell["index"]] + timedelta(weeks=1))
        for cell in cells
    ]
    total_demand = round(sum(c.demand for c in views), 4)
    total_supply = round(sum(c.supply for c in views), 4)
    over_weeks = [c for c in views if c.state == CAP_STATE_OVER]
    idle_weeks = [c for c in views if c.state == CAP_STATE_IDLE]
    percents = [c.percent for c in views if c.percent is not None]
    peak = max(percents) if percents else None
    peak_index = None
    if peak is not None:
        peak_index = next(c.index for c in views if c.percent == peak)

    return SimpleNamespace(
        **row_info,
        weeks=views,
        total_demand=total_demand,
        total_supply=total_supply,
        total_demand_str=fmt_capacity_days(total_demand),
        total_supply_str=fmt_capacity_days(total_supply),
        utilization_percent=capacity_percent(total_demand, total_supply),
        utilization_str=fmt_capacity_percent(capacity_percent(total_demand, total_supply)),
        peak_percent=peak,
        peak_week_index=peak_index,
        weeks_over=len(over_weeks),
        weeks_idle=len(idle_weeks),
        first_over_date=over_weeks[0].start if over_weeks else None,
        total_over_days=round(sum(c.over_days for c in views), 4),
        is_over=bool(over_weeks),
    )


# ---------------------------------------------------------------------------
# The computation
# ---------------------------------------------------------------------------

def build_capacity(
    project=None,
    tasks=None,
    members=None,
    *,
    weeks=None,
    window_start=None,
    window_end=None,
    min_weeks=CAPACITY_MIN_WEEKS,
    max_weeks=CAPACITY_MAX_WEEKS,
    remaining_effort=True,
    today=None,
):
    """Compute supply vs demand per member per week.

    :param project: supplies ``calendar`` (working days + holidays); ``None`` means a
        plain Mon-Fri, holiday-free week.
    :param tasks: :class:`~app.models.Task`-like objects. Milestones are kept for the
        window but contribute no demand; unassigned work is collected in its own row.
    :param members: :class:`~app.models.TeamMember`-like objects, in display order.
        Their ``absences`` (and ``capacity_percent``) drive supply.
    :param weeks: exact number of weeks to chart; overrides the auto window.
    :param window_start / window_end: restrict the window (``window_end`` is inclusive
        of the week it falls in). Pass ``window_start=date.today()`` for a
        "what is left of the plan" view.
    :param remaining_effort: ``True`` (default) weights demand by the effort still to
        do, so delivered work stops loading its owner; ``False`` is the full plan.
    :returns: ``SimpleNamespace`` with ``weeks`` (columns), ``rows`` (one per member),
        ``unassigned_row``, ``ghosts``, per-week ``totals`` and the headline figures
        ``members_over`` / ``peak_percent``. ``span_start`` / ``span_end`` stay the raw
        project dates, unrounded, for headers that talk about the plan rather than the
        chart.
    """
    cal = _calendar_for(project)
    tasks = list(tasks or [])
    members = list(members or [])
    mode = MODE_REMAINING if remaining_effort else MODE_FULL

    spans = []
    for task in tasks:
        span = _task_span(task, cal)
        if span:
            spans.append(span)

    week_mondays, truncated = _planning_window(
        spans, cal, window_start, window_end, weeks, min_weeks, max_weeks, today
    )
    week_count = len(week_mondays)
    bounds = [(m, m + timedelta(weeks=1)) for m in week_mondays]
    span_start = min(s for s, _ in spans) if spans else None
    span_end = max(e for _, e in spans) if spans else None

    columns = [
        SimpleNamespace(
            index=i,
            start=monday,
            end=monday + timedelta(weeks=1),
            label=week_label(monday),
            full_label=week_full_label(monday),
            head=week_head_label(monday),
            month_label=month_label(monday),
            working_days=count_workdays(monday, monday + timedelta(weeks=1), calendar=cal),
            is_past=bool(today and monday + timedelta(weeks=1) <= today),
            is_current=bool(today and monday <= today < monday + timedelta(weeks=1)),
            is_future=bool(today and monday > today),
        )
        for i, monday in enumerate(week_mondays)
    ]

    # --- supply ----------------------------------------------------------
    member_rows = {}
    for member in members:
        cells = [_new_cell(i) for i in range(week_count)]
        fraction = _capacity_fraction(member)
        for i, (monday, week_end) in enumerate(bounds):
            working = count_workdays(monday, week_end, calendar=cal)
            away = min(working, _absence_days(cal, member, monday, week_end))
            cells[i]["supply"] = max(0.0, (working - away) * fraction)
        member_rows[_norm_name(getattr(member, "name", ""))] = cells

    unassigned_cells = [_new_cell(i) for i in range(week_count)]
    # The unowned row has no capacity of its own; measuring it against one hypothetical
    # full-time person makes its percentage read as "how much of a week nobody owns yet".
    for i, (monday, week_end) in enumerate(bounds):
        unassigned_cells[i]["supply"] = float(
            count_workdays(monday, week_end, calendar=cal)
        )
    ghosts = {}

    # --- demand ----------------------------------------------------------
    for task in tasks:
        if getattr(task, "is_milestone", False):
            continue  # zero duration → zero effort
        span = _task_span(task, cal)
        if not span:
            continue
        start, end = span

        weight = _allocation_fraction(task)
        if mode == MODE_REMAINING:
            weight *= _remaining_factor(task)
        if weight <= _EPS:
            continue

        name = _norm_name(getattr(task, "responsible", ""))
        if not name:
            cells = unassigned_cells
        elif name in member_rows:
            cells = member_rows[name]
        else:
            ghost = ghosts.setdefault(
                name,
                {"name": (getattr(task, "responsible", "") or "").strip(), "demand": 0.0, "codes": []},
            )
            cells = None

        for i, (monday, week_end) in enumerate(bounds):
            first = max(monday, start)
            last = min(week_end, end)
            if last <= first:
                continue
            days = count_workdays(first, last, calendar=cal)
            if days <= 0:
                continue
            load = days * weight
            if cells is not None:
                cell = cells[i]
                cell["demand"] += load
                code = _task_code(task)
                if code and code not in cell["codes"]:
                    cell["codes"].append(code)
            else:
                ghost["demand"] += load
                code = _task_code(task)
                if code and code not in ghost["codes"]:
                    ghost["codes"].append(code)

    # --- rows ------------------------------------------------------------
    rows = []
    for member in members:
        name = (getattr(member, "name", "") or "").strip()
        info = {
            "key": f"member-{getattr(member, 'id', name)}",
            "kind": ROW_MEMBER,
            "member": member,
            "member_id": getattr(member, "id", None),
            "name": name,
            "role": getattr(member, "role", None) or "",
            "capacity_percent": (
                int(getattr(member, "capacity_percent", 100) or 100)
            ),
            "is_ghost": False,
            "is_unassigned": False,
        }
        rows.append(
            _row_from_cells(info, member_rows[_norm_name(name)], week_mondays)
        )

    unassigned_info = {
        "key": "unassigned",
        "kind": ROW_UNASSIGNED,
        "member": None,
        "member_id": None,
        "name": UNASSIGNED_LABEL,
        "role": "work with no owner",
        "capacity_percent": 100,
        "is_ghost": False,
        "is_unassigned": True,
    }
    unassigned_row = _row_from_cells(unassigned_info, unassigned_cells, week_mondays)
    if not members and unassigned_row.total_demand <= _EPS:
        # Nothing to show: no resources configured and no unowned work.
        unassigned_row = None

    ghost_rows = sorted(ghosts.values(), key=lambda g: (g["name"] or "").lower())
    for ghost in ghost_rows:
        ghost["demand"] = round(ghost["demand"], 4)

    # --- team totals -----------------------------------------------------
    totals = []
    for i in range(week_count):
        demand = round(sum(r.weeks[i].demand for r in rows), 4)
        supply = round(sum(r.weeks[i].supply for r in rows), 4)
        totals.append(
            SimpleNamespace(
                index=i,
                start=columns[i].start,
                end=columns[i].end,
                label=columns[i].label,
                demand=demand,
                supply=supply,
                demand_str=fmt_capacity_days(demand),
                supply_str=fmt_capacity_days(supply),
                percent=capacity_percent(demand, supply),
                percent_str=fmt_capacity_percent(capacity_percent(demand, supply)),
                state=capacity_state(demand, supply),
            )
        )

    team_demand = round(sum(r.total_demand for r in rows), 4)
    team_supply = round(sum(r.total_supply for r in rows), 4)
    all_percents = [c.percent for r in rows for c in r.weeks if c.percent is not None]
    peak = max(all_percents) if all_percents else None
    peak_index = None
    if peak is not None:
        for row in rows:
            for cell in row.weeks:
                if cell.percent == peak:
                    peak_index = cell.index
                    break
            if peak_index is not None:
                break

    return SimpleNamespace(
        mode=mode,
        bucket=BUCKET_WEEKS,
        weeks=columns,
        rows=rows,
        unassigned_row=unassigned_row,
        ghosts=ghost_rows,
        totals=totals,
        window_start=columns[0].start if columns else None,
        window_end=columns[-1].end if columns else None,
        window_weeks=week_count,
        window_truncated=truncated,
        span_start=span_start,
        span_end=span_end,
        members_total=len(rows),
        members_over=sum(1 for r in rows if r.is_over),
        members_idle=sum(1 for r in rows if r.weeks and all(c.state in (CAP_STATE_IDLE, CAP_STATE_NONE) for c in r.weeks)),
        peak_percent=peak,
        peak_week_index=peak_index,
        total_demand=team_demand,
        total_supply=team_supply,
        utilization_percent=capacity_percent(team_demand, team_supply),
        unassigned_demand=round(unassigned_row.total_demand, 4) if unassigned_row else 0.0,
        ghost_demand=round(sum(g["demand"] for g in ghost_rows), 4),
        has_capacity_data=bool(members),
        has_work=bool(spans),
        today=today,
        calendar=cal,
    )


def find_week_cell(row, day):
    """The week cell of ``row`` that contains ``day``, or ``None`` outside the window.

    Used wherever a single date has to be explained by its week: the availability
    list in Settings, the tooltip on an over-loaded task.
    """
    if row is None or day is None:
        return None
    for cell in row.weeks:
        if cell.start <= day < cell.end:
            return cell
    return None


def find_member_row(capacity, name):
    """The row for a member name (case-insensitive), or ``None``."""
    if capacity is None:
        return None
    needle = _norm_name(name)
    if not needle:
        return None
    for row in capacity.rows:
        if _norm_name(row.name) == needle:
            return row
    return None


def worst_week(capacity, name, start=None, end=None):
    """The most-loaded week for ``name`` within ``[start, end]`` — the grid's overload hint.

    Used by the task grid to mark a row whose assignee is already over capacity in the
    task's own window; returns ``None`` when the person has no capacity row. Equal
    percentages resolve to the **earliest** week, which is where the crunch starts.
    """
    row = find_member_row(capacity, name)
    if row is None:
        return None
    candidates = list(row.weeks)
    if start:
        candidates = [c for c in candidates if c.end > start]
    if end:
        candidates = [c for c in candidates if c.start <= end]
    scored = [c for c in candidates if c.percent is not None]
    if not scored:
        return None
    return max(scored, key=lambda c: (c.percent, -c.index))
