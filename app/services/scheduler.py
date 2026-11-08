"""PERT schedule calculation, forward-pass DAG scheduling, and dependency validation."""

from datetime import datetime
from collections import deque
from ..db import db
from ..models import Project, Task, TASK_KIND_TASK, TASK_KIND_MILESTONE
from ..workdays import count_workdays, next_workday, diff_workdays


def fmt_date(d):
    """Format a date as '01 Jan 2026'."""
    return d.strftime("%d %b %Y") if d else ""


def recalculate_project(project_id):
    """Recompute start dates for every task in the project.

    Dates follow the 5-day week: durations count working days and no task ever
    starts on a Saturday or Sunday.

    Rule: a task's end date is ``start_date + duration_days`` **working days**
    (the first day a successor may start), and every task must start on or
    after the latest end date of its predecessors. Whenever a task or its
    predecessor set changes, this pushes affected dates forward along the
    dependency chain so the whole project stays consistent; the grid is the
    source of truth and the diagram simply reads the data back.

    * Every start date is first rolled forward to the next working day, so a
      date entered (or inherited) on a weekend lands on the Monday.
    * Tasks with no predecessors keep their entered start date: they anchor
      the schedule.
    * Tasks are processed in dependency order (predecessors first), so a
      change cascades through the full chain in a single pass.
    * Dates only ever move forward: shortening a predecessor never pulls a
      successor earlier.

    Only the start dates of tasks inside this session are touched; the caller
    commits. Returns the codes whose start date was moved.
    """
    by_code = {t.code: t for t in Task.query.filter_by(project_id=project_id)}
    if not by_code:
        return []

    project = Project.query.get(project_id)
    cal = project.calendar if project else None

    moved = set()

    # Pass 1 - roll non-working days and holidays onto the next working day.
    for code, task in by_code.items():
        workday = next_workday(task.start_date, calendar=cal)
        if workday != task.start_date:
            task.start_date = workday
            moved.add(code)

    dependents = {code: [] for code in by_code}
    indegree = {}
    for code, task in by_code.items():
        preds = [p for p in task.predecessor_codes() if p in by_code and p != code]
        indegree[code] = len(preds)
        for p in preds:
            dependents[p].append(code)

    queue = deque(sorted(c for c in by_code if indegree[c] == 0))
    while queue:
        code = queue.popleft()
        task = by_code[code]
        preds = [by_code[p] for p in task.predecessor_codes()
                 if p in by_code and p != code]
        if preds:
            # Every predecessor's end date respects the calendar.
            # Earliest successor start is a working day on or after max(p.end_date).
            earliest = next_workday(max(p.end_date for p in preds), calendar=cal)
            if task.start_date < earliest:
                task.start_date = earliest
                moved.add(code)
        for d in dependents[code]:
            indegree[d] -= 1
            if indegree[d] == 0:
                queue.append(d)
    # Tasks left unprocessed belong to a predecessor cycle; they keep their
    # entered dates (cycles cannot be saved, validate_task rejects them).
    return sorted(moved)


def validate_task(task, project_id, exclude_task_id=None, check_schedule=True):
    """Validate task duration, milestones, predecessors, cycles, and schedule conflicts."""
    errors = []
    if not task.start_date:
        errors.append("Start date is missing or not a valid date.")
    is_milestone = getattr(task, "kind", TASK_KIND_TASK) == TASK_KIND_MILESTONE
    if is_milestone:
        if task.duration_days != 0:
            errors.append("Milestones must have zero duration.")
    elif task.duration_days is None or task.duration_days < 1:
        errors.append("Duration must be an integer >= 1.")

    if task.predecessors:
        all_tasks = Task.query.filter_by(project_id=project_id).all()
        if exclude_task_id:
            all_tasks = [t for t in all_tasks if t.id != exclude_task_id]
        codes = task.predecessor_codes()
        valid_codes = {t.code for t in all_tasks}
        unknown = [c for c in codes if c not in valid_codes]
        if unknown:
            errors.append(f"Unknown predecessor(s): {', '.join(unknown)}")

        task_code = task.code
        if task_code in codes:
            errors.append("A task cannot be its own predecessor.")
        else:
            graph = {t.code: t.predecessor_codes() for t in all_tasks}
            if task_code:
                graph[task_code] = codes

            def has_cycle(start, visited=None, rec_stack=None):
                visited = visited or set()
                rec_stack = rec_stack or set()
                visited.add(start)
                rec_stack.add(start)
                for pred in graph.get(start, []):
                    if pred not in visited:
                        if has_cycle(pred, visited, rec_stack):
                            return True
                    elif pred in rec_stack:
                        return True
                rec_stack.remove(start)
                return False

            for code in list(graph.keys()):
                if has_cycle(code):
                    errors.append("Cycle detected in predecessor dependencies.")
                    break

            # Scheduling violation
            if check_schedule and codes and task.start_date:
                all_tasks_map = {t.code: t for t in all_tasks}
                pred_end_dates = []
                for c in codes:
                    pred_task = all_tasks_map.get(c)
                    if pred_task and pred_task.end_date:
                        pred_end_dates.append(pred_task.end_date)
                if pred_end_dates and task.start_date:
                    max_end = max(pred_end_dates)
                    if task.start_date < max_end:
                        errors.append(f"Task starts before predecessor ends (needs start >= {fmt_date(max_end)}).")
    return errors


def validate_predecessors_change(task, project_id, new_preds):
    """Return ``(normalized_predecessors, errors)`` for a dependency-only edit."""
    normalized = (new_preds or "").strip() or None
    temp = Task(
        project_id=project_id,
        code=task.code,
        kind=task.kind,
        lane_id=task.lane_id,
        name=task.name,
        start_date=task.start_date,
        duration_days=task.duration_days,
        responsible=task.responsible,
        predecessors=normalized,
        started=task.started,
        done=task.done,
    )
    errors = validate_task(
        temp, project_id, exclude_task_id=task.id, check_schedule=False
    )
    return normalized, errors


def project_date_stats(tasks, calendar=None):
    """Project-level totals shown above the Lanes panel.

    `total_effort` is the sum of all task durations, and `total_duration` the
    span from the earliest start date to the latest end date - both counted in
    **working days** (excluding non-working days and holidays according to the calendar).
    """
    if not tasks:
        return 0, 0, None, None
    total_effort = sum(
        t.duration_days for t in tasks if t.kind != TASK_KIND_MILESTONE
    )
    project_start = min(t.start_date for t in tasks)
    project_end = max(t.end_date for t in tasks if t.end_date)
    cal = calendar
    if cal is None and tasks and hasattr(tasks[0], "project") and getattr(tasks[0], "project", None):
        cal = tasks[0].project.calendar
    total_duration = count_workdays(project_start, project_end, calendar=cal)
    return total_effort, total_duration, project_start, project_end


def task_successors(task, all_tasks):
    """Return tasks that have `task` listed as a predecessor, ordered by start date."""
    code = task.code
    return sorted(
        [t for t in all_tasks if code in t.predecessor_codes()],
        key=lambda t: (t.start_date, t.code),
    )


def set_project_baseline(project_id):
    """Snapshot the current schedule as the project's baseline."""
    project = Project.query.get(project_id)
    if not project:
        return 0
    project.baseline_set_at = datetime.utcnow()
    tasks = Task.query.filter_by(project_id=project.id).all()
    count = 0
    for t in tasks:
        t.baseline_start_date = t.start_date
        t.baseline_end_date = t.end_date
        t.baseline_duration_days = t.duration_days
        count += 1
    db.session.commit()
    return count


def clear_project_baseline(project_id):
    """Clear stored baseline dates for the project and its tasks."""
    project = Project.query.get(project_id)
    if not project:
        return
    project.baseline_set_at = None
    tasks = Task.query.filter_by(project_id=project.id).all()
    for t in tasks:
        t.baseline_start_date = None
        t.baseline_end_date = None
        t.baseline_duration_days = None
    db.session.commit()


def compute_critical_path(tasks, calendar=None):
    """Compute critical path tasks and dependency edges using the Critical Path Method (CPM).

    Returns:
        critical_task_codes: set of task codes on the critical path.
        critical_edges: set of (pred_code, succ_code) tuples on the critical path.
    """
    if not tasks:
        return set(), set()

    by_code = {t.code: t for t in tasks}
    cal = calendar
    if cal is None and tasks and hasattr(tasks[0], "project") and getattr(tasks[0], "project", None):
        cal = tasks[0].project.calendar

    valid_starts = [t.start_date for t in tasks if t.start_date]
    valid_ends = [t.end_date for t in tasks if t.end_date]
    if not valid_starts or not valid_ends:
        return set(), set()

    project_start = min(valid_starts)
    project_end = max(valid_ends)
    project_duration = count_workdays(project_start, project_end, calendar=cal)

    es = {}
    ef = {}
    for code, t in by_code.items():
        if t.start_date and t.end_date:
            es[code] = count_workdays(project_start, t.start_date, calendar=cal)
            ef[code] = count_workdays(project_start, t.end_date, calendar=cal)
        else:
            es[code] = 0
            ef[code] = 0

    succs = {code: [] for code in by_code}
    preds_map = {code: [] for code in by_code}
    for code, t in by_code.items():
        for p in t.predecessor_codes():
            if p in by_code and p != code:
                succs[p].append(code)
                preds_map[code].append(p)

    indegree = {code: len(preds_map[code]) for code in by_code}
    queue = deque(sorted(c for c in by_code if indegree[c] == 0))
    topo_order = []
    while queue:
        c = queue.popleft()
        topo_order.append(c)
        for s in succs[c]:
            indegree[s] -= 1
            if indegree[s] == 0:
                queue.append(s)

    remaining = [c for c in by_code if c not in topo_order]
    topo_order.extend(remaining)

    # Backward pass
    lf = {}
    ls = {}
    for code in reversed(topo_order):
        t = by_code[code]
        dur = 0 if t.is_milestone else (t.duration_days or 0)
        if not succs[code]:
            # Tasks ending before the project finish have float proportional to how early they finish
            lf[code] = project_duration
        else:
            lf[code] = min(ls[s] for s in succs[code])
        ls[code] = lf[code] - dur

    critical_task_codes = set()
    for code in by_code:
        # Task is on the critical path if total float is 0
        total_float = ls.get(code, 0) - es.get(code, 0)
        if total_float == 0 and ef.get(code, 0) > 0:
            critical_task_codes.add(code)

    # If all tasks had float 0 but some don't lead to project_end, filter to those that reach project_end
    reaches_end = {code for code, t in by_code.items() if t.end_date == project_end and code in critical_task_codes}
    active_back = deque(reaches_end)
    verified_critical_tasks = set(reaches_end)
    while active_back:
        curr = active_back.popleft()
        for p in preds_map[curr]:
            if p in critical_task_codes and (by_code[p].end_date == by_code[curr].start_date or ef[p] == es[curr]):
                if p not in verified_critical_tasks:
                    verified_critical_tasks.add(p)
                    active_back.append(p)

    final_critical_tasks = verified_critical_tasks if verified_critical_tasks else critical_task_codes

    critical_edges = set()
    for code in final_critical_tasks:
        for p in preds_map[code]:
            if p in final_critical_tasks:
                if by_code[p].end_date == by_code[code].start_date or ef[p] == es[code]:
                    critical_edges.add((p, code))

    return final_critical_tasks, critical_edges


