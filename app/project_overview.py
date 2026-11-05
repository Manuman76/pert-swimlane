"""Project dashboard metrics for the Overview tab."""

from datetime import date
from types import SimpleNamespace

from .models import RAID_KIND_RISK, RAID_STATUS_OPEN
from .requirement_trace import REQ_COVERAGE_NOT_COVERED, compute_coverage
from .workdays import count_workdays


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


def _risk_tier(probability, impact):
    p = (probability or "").lower()
    i = (impact or "").lower()
    if p == "high" and i == "high":
        return "critical"
    if p == "high" or i == "high":
        return "high"
    return None


def build_project_overview(tasks, requirements, raid_items):
    """Aggregate task, requirement, risk, milestone, and schedule figures."""
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
    milestones_list = []
    for m in milestones:
        milestones_list.append(SimpleNamespace(
            id=getattr(m, "id", None),
            code=m.code,
            name=m.name,
            start_date=m.start_date,
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
        schedule_slip_days = count_workdays(target_end, forecast_end)

    total_schedule_workdays = count_workdays(project_start, forecast_end) if (project_start and forecast_end) else 0

    if total_tasks > 0 and completed == total_tasks:
        health_status = "completed"
        health_label = "Complete"
        health_badge_class = "health-badge-success"
        health_summary = "All planned tasks have been delivered."
    elif risk_critical > 0 or schedule_slip_days > 5:
        health_status = "critical"
        health_label = "Needs Attention"
        health_badge_class = "health-badge-danger"
        reasons = []
        if risk_critical > 0:
            reasons.append(f"{risk_critical} critical risk{'s' if risk_critical > 1 else ''}")
        if schedule_slip_days > 0:
            reasons.append(f"{schedule_slip_days} workday{'s' if schedule_slip_days > 1 else ''} slip")
        health_summary = f"Flagged: {', '.join(reasons)}."
    elif risk_high > 0 or schedule_slip_days > 0 or (req_total > 0 and req_covered == 0):
        health_status = "at_risk"
        health_label = "At Risk"
        health_badge_class = "health-badge-warning"
        reasons = []
        if schedule_slip_days > 0:
            reasons.append(f"{schedule_slip_days} workday{'s' if schedule_slip_days > 1 else ''} schedule slip")
        if risk_high > 0:
            reasons.append(f"{risk_high} high risk{'s' if risk_high > 1 else ''}")
        if req_total > 0 and req_covered == 0:
            reasons.append("no requirements covered")
        health_summary = f"Notice: {', '.join(reasons)}."
    else:
        health_status = "on_track"
        health_label = "On Track"
        health_badge_class = "health-badge-success"
        health_summary = "Schedule, deliverables, and risk profile are tracking within expectations."

    req_coverage_percent = round(100 * req_covered / req_total) if req_total else 100

    return SimpleNamespace(
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
