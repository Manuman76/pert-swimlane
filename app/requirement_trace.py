"""Requirement traceability: coverage derived from linked task status."""

from types import SimpleNamespace

REQ_COVERAGE_NOT_COVERED = "not_covered"
REQ_COVERAGE_PLANNED = "planned"
REQ_COVERAGE_IN_PROGRESS = "in_progress"
REQ_COVERAGE_IMPLEMENTED = "implemented"

REQ_COVERAGE_META = {
    REQ_COVERAGE_NOT_COVERED: {
        "label": "Not covered",
        "emoji": "\U0001f7e5",  # 🟥
    },
    REQ_COVERAGE_PLANNED: {
        "label": "Planned",
        "emoji": "\U0001f7e7",  # 🟧
    },
    REQ_COVERAGE_IN_PROGRESS: {
        "label": "In progress",
        "emoji": "\U0001f7e6",  # 🟦
    },
    REQ_COVERAGE_IMPLEMENTED: {
        "label": "Implemented",
        "emoji": "\U0001f7e9",  # 🟩
    },
}

MOSCOW_SHORT = {
    "must": "Must",
    "should": "Should",
    "could": "Could",
    "wont": "Won't",
}


def linked_tasks_sorted(requirement):
    return sorted(requirement.tasks, key=lambda t: t.code)


def compute_coverage(tasks):
    """Return coverage state from linked tasks (empty list → not covered)."""
    linked = list(tasks)
    if not linked:
        return REQ_COVERAGE_NOT_COVERED
    if all(t.status == "done" for t in linked):
        return REQ_COVERAGE_IMPLEMENTED
    if any(t.status in ("started", "done") for t in linked):
        return REQ_COVERAGE_IN_PROGRESS
    return REQ_COVERAGE_PLANNED


def coverage_status_display(coverage, tasks):
    """Short status column text for the traceability matrix."""
    if coverage == REQ_COVERAGE_NOT_COVERED:
        return "\u2014"
    if coverage == REQ_COVERAGE_IMPLEMENTED:
        return "Done"
    if coverage == REQ_COVERAGE_PLANNED:
        return "Planned"
    linked = list(tasks)
    if linked and all(t.status == "started" for t in linked):
        return "Started"
    return "In progress"


def task_codes_display(tasks):
    linked = list(tasks)
    if not linked:
        return "none"
    return ", ".join(t.code for t in sorted(linked, key=lambda t: t.code))


def traceability_for(requirement):
    linked = linked_tasks_sorted(requirement)
    coverage = compute_coverage(linked)
    meta = REQ_COVERAGE_META[coverage]
    return SimpleNamespace(
        linked_tasks=linked,
        task_codes=task_codes_display(linked),
        coverage=coverage,
        coverage_label=meta["label"],
        coverage_emoji=meta["emoji"],
        status_display=coverage_status_display(coverage, linked),
        priority_short=MOSCOW_SHORT.get(requirement.moscow, requirement.moscow),
    )
