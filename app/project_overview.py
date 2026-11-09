"""Project dashboard metrics for the Overview tab."""

from datetime import date
from types import SimpleNamespace

from .capacity import fmt_capacity_days, fmt_capacity_percent
from .models import RAID_KIND_RISK, RAID_STATUS_OPEN
from .requirement_trace import REQ_COVERAGE_NOT_COVERED, compute_coverage
from .workdays import count_workdays, diff_workdays

# Health thresholds. Everything here compares like with like: a working day of slip is
# one unit, an overloaded week of a named person is another, and the two are never added
# together into one number.
#
# Any slip or overdue day is a warning (``at_risk``); only a slip the steering group has
# to react to (``> CRITICAL_SLIP_WORKDAYS``) is ``critical``.
CRITICAL_SLIP_WORKDAYS = 5
# Capacity, read from the grid app/capacity.py computes for the whole plan.
CAPACITY_AT_RISK_MEMBERS_OVER = 1
CAPACITY_CRITICAL_WEEKS_OVER = 2
CAPACITY_CRITICAL_PEAK_PERCENT = 200


def fmt_overview_day(d, include_year=False):
    """Compact date for the dashboard (e.g. ``8 Nov`` or ``8 Nov 2026``)."""
    if not d:
        return ""
    text = d.strftime("%d %b")
    if text.startswith("0"):
        text = text[1:]
    if include_year:
        return f"{text} {d.year}"
    return text


def fmt_variance(days):
    """Format signed days into concise badge text, e.g. +2d, -3d, 0d."""
    if days is None:
        return ""
    if days > 0:
        return f"+{days}d"
    elif days < 0:
        return f"{days}d"
    return "0d"


def _risk_tier(probability, impact):
    p = (probability or "").lower()
    i = (impact or "").lower()
    if p == "high" and i == "high":
        return "critical"
    if p == "high" or i == "high":
        return "high"
    return None


def capacity_health(capacity):
    """How a capacity model moves project health: ``(status, reasons)``.

    ``status`` is ``"none"``, ``"at_risk"`` or ``"critical"``; ``reasons`` are the
    one-liners the health summary appends. Over 100 % is a colour in this app, never a
    refusal, so capacity can only ever *raise* a status, never soften one, and it says
    nothing at all until there is a team to measure: a name the roster does not know has
    no availability to exceed (the Capacity tab keeps that in a sentence).

    * **at_risk** — anyone is booked past a full week somewhere in the plan.
    * **critical** — a *sustained* crunch: the same person over in
      :data:`CAPACITY_CRITICAL_WEEKS_OVER` weeks, or a single week at
      :data:`CAPACITY_CRITICAL_PEAK_PERCENT` or more. One overloaded week is a
      re-planning job; a run of them, or double the load, is a steering problem.
    """
    if capacity is None or not getattr(capacity, "members_total", 0):
        return "none", []

    rows = list(getattr(capacity, "rows", None) or [])
    members_over = int(getattr(capacity, "members_over", 0) or 0)
    members_total = int(getattr(capacity, "members_total", 0) or 0)
    peak = getattr(capacity, "peak_percent", None)
    worst = max(rows, key=lambda r: getattr(r, "weeks_over", 0) or 0, default=None)
    weeks_over = (getattr(worst, "weeks_over", 0) or 0) if worst else 0

    reasons = []
    if members_over >= CAPACITY_AT_RISK_MEMBERS_OVER:
        reasons.append(
            f"{members_over} of {members_total} people over capacity"
            + (f" (peak {peak}%)" if peak and peak > 100 else "")
        )
    crunch = weeks_over >= CAPACITY_CRITICAL_WEEKS_OVER or (
        peak is not None and peak >= CAPACITY_CRITICAL_PEAK_PERCENT
    )
    if crunch:
        if weeks_over >= CAPACITY_CRITICAL_WEEKS_OVER:
            who = (getattr(worst, "name", "") or "Someone").strip()
            reasons.insert(
                0,
                f"{who} is over capacity in {weeks_over} weeks",
            )
        if peak is not None and peak >= CAPACITY_CRITICAL_PEAK_PERCENT:
            reasons.append(f"peak load {peak}%")
        return "critical", reasons
    if members_over >= CAPACITY_AT_RISK_MEMBERS_OVER:
        return "at_risk", reasons
    return "none", []


def _capacity_fields(capacity, capacity_status):
    """The Resources card's figures, pre-formatted so templates stay arithmetic-free."""
    if capacity is None:
        return {
            "has_capacity": False,
            "capacity_crunch": False,
            "members_over": 0,
            "capacity_members_total": 0,
            "capacity_peak_percent": None,
            "capacity_peak_str": "—",
            "capacity_window_weeks": 0,
            "capacity_unassigned_days": 0.0,
            "capacity_unassigned_str": "",
        }
    peak = getattr(capacity, "peak_percent", None)
    unassigned = float(getattr(capacity, "unassigned_demand", 0) or 0)
    return {
        "has_capacity": bool(getattr(capacity, "members_total", 0)),
        "capacity_crunch": capacity_status == "critical",
        "members_over": int(getattr(capacity, "members_over", 0) or 0),
        "capacity_members_total": int(getattr(capacity, "members_total", 0) or 0),
        "capacity_peak_percent": peak,
        "capacity_peak_str": fmt_capacity_percent(peak),
        "capacity_window_weeks": int(getattr(capacity, "window_weeks", 0) or 0),
        "capacity_unassigned_days": round(unassigned, 1),
        "capacity_unassigned_str": fmt_capacity_days(unassigned) if unassigned else "",
    }


def build_project_overview(tasks, requirements, raid_items, project=None, capacity=None):
    """Aggregate task, requirement, risk, milestone, schedule, and baseline figures."""
    cal = getattr(project, "calendar", None) if project else None
    work_tasks = [t for t in tasks if not t.is_milestone]
    total_tasks = len(work_tasks)
    completed = sum(1 for t in work_tasks if t.status == "done")
    in_progress = sum(1 for t in work_tasks if t.status == "started")
    not_started = sum(1 for t in work_tasks if t.status == "not_started")
    progress_percent = round(100 * completed / total_tasks) if total_tasks else 0

    req_total = len(requirements)
    req_uncovered = sum(
        1 for r in requirements if compute_coverage(r.tasks) == REQ_COVERAGE_NOT_COVERED
    )
    req_covered = req_total - req_uncovered

    risks = [
        item for item in raid_items
        if item.kind == RAID_KIND_RISK and item.status == RAID_STATUS_OPEN
    ]
    risk_critical = sum(1 for r in risks if _risk_tier(r.probability, r.impact) == "critical")
    risk_high = sum(1 for r in risks if _risk_tier(r.probability, r.impact) == "high")

    milestones = sorted(
        (t for t in tasks if t.is_milestone),
        key=lambda t: (t.start_date, t.code),
    )
    today = date.today()
    next_milestone = None
    for m in milestones:
        if not m.done and m.start_date >= today:
            next_milestone = m
            break
    if next_milestone is None:
        for m in milestones:
            if not m.done:
                next_milestone = m
                break

    milestones_total = len(milestones)
    milestones_completed = sum(1 for m in milestones if m.done)
    milestones_percent = round(100 * milestones_completed / milestones_total) if milestones_total else 0

    has_baseline = bool(
        getattr(project, "baseline_set_at", None)
        or any(getattr(t, "baseline_start_date", None) or getattr(t, "baseline_end_date", None) for t in tasks)
    )
    baseline_set_at = getattr(project, "baseline_set_at", None)

    milestones_list = []
    for m in milestones:
        base_date = getattr(m, "baseline_start_date", None)
        var_days = diff_workdays(base_date, m.start_date, calendar=cal) if (base_date and m.start_date) else 0
        milestones_list.append(SimpleNamespace(
            id=getattr(m, "id", None),
            code=m.code,
            name=m.name,
            start_date=m.start_date,
            baseline_date=base_date,
            variance_days=var_days,
            variance_str=fmt_variance(var_days),
            has_baseline=bool(base_date),
            done=m.done,
            is_next=(m == next_milestone),
            is_overdue=(not m.done and m.start_date < today),
        ))

    open_issues = sum(
        1 for item in raid_items
        if item.kind == "issue" and item.status == RAID_STATUS_OPEN
    )

    if tasks:
        project_start = min(t.start_date for t in tasks)
        project_end = max(t.end_date for t in tasks if t.end_date)
    else:
        project_start = project_end = None

    milestone_dates = [m.start_date for m in milestones if m.start_date]
    target_end = max(milestone_dates) if milestone_dates else project_end
    forecast_end = project_end
    schedule_slip_days = 0
    if target_end and forecast_end and forecast_end > target_end:
        schedule_slip_days = count_workdays(target_end, forecast_end, calendar=cal)

    total_schedule_workdays = count_workdays(project_start, forecast_end, calendar=cal) if (project_start and forecast_end) else 0

    # Baseline target and overall schedule variance
    baseline_milestone_dates = [m.baseline_start_date for m in milestones if getattr(m, "baseline_start_date", None)]
    if baseline_milestone_dates:
        baseline_target_end = max(baseline_milestone_dates)
    elif tasks and any(getattr(t, "baseline_end_date", None) for t in tasks):
        baseline_target_end = max(t.baseline_end_date for t in tasks if getattr(t, "baseline_end_date", None))
    else:
        baseline_target_end = None

    schedule_variance_days = None
    if has_baseline:
        milestones_with_baseline = [m for m in milestones_list if m.has_baseline]
        if milestones_with_baseline:
            # The final baselined milestone is the anchor delivery gate
            schedule_variance_days = milestones_with_baseline[-1].variance_days
        elif baseline_target_end and forecast_end:
            schedule_variance_days = diff_workdays(baseline_target_end, forecast_end, calendar=cal)
        else:
            schedule_variance_days = 0

    effective_slip = schedule_slip_days
    if has_baseline and schedule_variance_days is not None:
        effective_slip = max(schedule_slip_days, max(0, schedule_variance_days))

    # Overdue checks against today
    incomplete_tasks = [t for t in work_tasks if t.status != "done"]
    is_project_overdue = bool(forecast_end and forecast_end < today and incomplete_tasks)
    overdue_days = count_workdays(forecast_end, today, calendar=cal) if is_project_overdue else 0

    overdue_milestones = [m for m in milestones_list if m.is_overdue]
    max_milestone_overdue_days = max(
        (count_workdays(m.start_date, today, calendar=cal) for m in overdue_milestones),
        default=0
    )

    # People are weighed in before the status is chosen, but never first: a schedule or
    # risk fact is allowed to outrank them, and capacity can only raise a status.
    capacity_status, capacity_reasons = capacity_health(capacity)

    if total_tasks > 0 and completed == total_tasks:
        health_status = "completed"
        health_label = "Complete"
        health_badge_class = "health-badge-success"
        health_summary = "All planned tasks have been delivered."
    elif (
        risk_critical > 0
        or effective_slip > CRITICAL_SLIP_WORKDAYS
        or overdue_days > CRITICAL_SLIP_WORKDAYS
        or max_milestone_overdue_days > CRITICAL_SLIP_WORKDAYS
        or capacity_status == "critical"
    ):
        health_status = "critical"
        health_label = "Needs Attention"
        health_badge_class = "health-badge-danger"
        reasons = []
        if is_project_overdue:
            reasons.append(f"finish date passed ({overdue_days} workday{'s' if overdue_days != 1 else ''} ago with {len(incomplete_tasks)} incomplete task{'s' if len(incomplete_tasks) != 1 else ''})")
        elif overdue_milestones:
            reasons.append(f"{len(overdue_milestones)} milestone{'s' if len(overdue_milestones) != 1 else ''} overdue")
        if risk_critical > 0:
            reasons.append(f"{risk_critical} critical risk{'s' if risk_critical > 1 else ''}")
        if schedule_variance_days and schedule_variance_days > 0:
            reasons.append(f"{schedule_variance_days} workday{'s' if schedule_variance_days != 1 else ''} variance vs baseline")
        elif schedule_slip_days > 0:
            reasons.append(f"{schedule_slip_days} workday{'s' if schedule_slip_days > 1 else ''} slip")
        reasons.extend(capacity_reasons)
        health_summary = f"Flagged: {', '.join(reasons)}."
    elif (
        risk_high > 0
        or effective_slip > 0
        or overdue_days > 0
        or overdue_milestones
        or (req_total > 0 and req_covered == 0)
        or capacity_status in ("at_risk", "critical")
    ):
        health_status = "at_risk"
        health_label = "At Risk"
        health_badge_class = "health-badge-warning"
        reasons = []
        if is_project_overdue:
            reasons.append(f"project finish date passed ({overdue_days} workday{'s' if overdue_days != 1 else ''} ago)")
        elif overdue_milestones:
            reasons.append(f"{len(overdue_milestones)} milestone{'s' if len(overdue_milestones) != 1 else ''} overdue")
        if schedule_variance_days and schedule_variance_days > 0:
            reasons.append(f"+{schedule_variance_days} workday{'s' if schedule_variance_days != 1 else ''} schedule variance")
        elif schedule_slip_days > 0:
            reasons.append(f"{schedule_slip_days} workday{'s' if schedule_slip_days > 1 else ''} schedule slip")
        if risk_high > 0:
            reasons.append(f"{risk_high} high risk{'s' if risk_high > 1 else ''}")
        if req_total > 0 and req_covered == 0:
            reasons.append("no requirements covered")
        reasons.extend(capacity_reasons)
        health_summary = f"Notice: {', '.join(reasons)}."
    else:
        health_status = "on_track"
        health_label = "On Track"
        health_badge_class = "health-badge-success"
        health_summary = "Schedule, deliverables, and risk profile are tracking within expectations."

    req_coverage_percent = round(100 * req_covered / req_total) if req_total else 100

    return SimpleNamespace(
        **_capacity_fields(capacity, capacity_status),
        capacity_status=capacity_status,
        progress_percent=progress_percent,
        tasks_total=total_tasks,
        tasks_completed=completed,
        tasks_in_progress=in_progress,
        tasks_not_started=not_started,
        requirements_total=req_total,
        requirements_covered=req_covered,
        requirements_uncovered=req_uncovered,
        requirements_percent=req_coverage_percent,
        risk_critical=risk_critical,
        risk_high=risk_high,
        risks_total_open=len(risks),
        issues_open=open_issues,
        next_milestone=next_milestone,
        milestones=milestones_list,
        milestones_total=milestones_total,
        milestones_completed=milestones_completed,
        milestones_percent=milestones_percent,
        has_baseline=has_baseline,
        baseline_set_at=baseline_set_at,
        baseline_target_end=baseline_target_end,
        schedule_variance_days=schedule_variance_days,
        is_project_overdue=is_project_overdue,
        overdue_days=overdue_days,
        schedule_start=project_start,
        schedule_target=target_end,
        schedule_forecast=forecast_end,
        schedule_slip_days=schedule_slip_days,
        schedule_workdays=total_schedule_workdays,
        has_schedule=bool(project_start and forecast_end),
        health_status=health_status,
        health_label=health_label,
        health_badge_class=health_badge_class,
        health_summary=health_summary,
    )
