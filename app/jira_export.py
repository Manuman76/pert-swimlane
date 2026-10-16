"""Export a project's tasks as a CSV file the Jira CSV importer can consume.

Jira's CSV import rules that shape this module:

* The header row must contain a ``Summary`` column (required by Jira).
* Links between imported issues are resolved through a shared "Issue ID"
  column: reference another row's ID in a link column and Jira creates the
  link after all rows are created.
* A link column's header names the link type; the value reads "this issue
  <link type> <referenced issue>". Dependencies are finish-to-start, so each
  task lists its predecessors under ``is blocked by``.
* Hierarchy and other optional fields are omitted: lanes map to Components,
  which needs no extra Jira setup.

Dates come straight from the scheduler, so they follow the 5-day week: a
duration counts working days and no start or due date falls on a Saturday or
Sunday.
"""

import csv
import io

# Columns Jira understands natively, followed by PERT-specific extras so no
# data is lost on the round trip.
FIELD_NAMES = [
    "Issue ID",
    "Summary",
    "Issue Type",
    "Component",
    "Assignee",
    "Description",
    "Due date",
    "Original Estimate",
    "Labels",
    # PERT scheduling details (map these to custom fields during import).
    "PERT Code",
    "Start Date",
    "Duration (working days)",
    "PERT Status",
    "Predecessors",
    # Link column: name it after the Jira link type you want created. It must
    # be a link type enabled in Jira (Admin -> Issues -> Issue linking).
    "is blocked by",
]

ISSUE_TYPE = "Task"

PERT_STATUS_LABELS = {
    "not_started": "Not started",
    "started": "Started",
    "done": "Done",
}


def _issue_id(task):
    """Stable, human-readable ID used to reference a task from link columns."""
    return task.code


def _estimate(task):
    """Jira duration format (``10d``) from the PERT duration.

    Jira's ``d`` unit is calendar days by default; the number is the working-day
    count from the grid, which is the figure to map onto a working-day custom
    field if Jira is configured with a work schedule.
    """
    days = task.duration_days or 0
    return f"{days}d" if days else ""


def export_project_csv(project, lanes, tasks):
    """Return the Jira-importable CSV for a project as a string.

    ``lanes`` supplies the component names; ``tasks`` is the project's tasks.
    Rows are ordered by lane order and then PERT code, and each task's
    predecessors are only referenced when they are part of this export (Jira
    cannot create a link to a row that is not in the file).
    """
    lane_names = {lane.id: lane.name for lane in lanes}
    known_codes = {task.code for task in tasks}

    lane_order = {lane.id: index for index, lane in enumerate(lanes)}
    ordered = sorted(
        tasks,
        key=lambda t: (lane_order.get(t.lane_id, len(lane_order)), t.code),
    )

    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=FIELD_NAMES, lineterminator="\n")
    writer.writeheader()

    for task in ordered:
        predecessors = [c for c in task.predecessor_codes() if c in known_codes]
        writer.writerow({
            "Issue ID": _issue_id(task),
            "Summary": task.name,
            "Issue Type": ISSUE_TYPE,
            "Component": lane_names.get(task.lane_id, ""),
            "Assignee": task.responsible or "",
            "Description": _description(task),
            "Due date": task.end_date.isoformat() if task.end_date else "",
            "Original Estimate": _estimate(task),
            "Labels": "",
            "PERT Code": task.code,
            "Start Date": task.start_date.isoformat() if task.start_date else "",
            "Duration (working days)": task.duration_days,
            "PERT Status": PERT_STATUS_LABELS.get(task.status, task.status),
            "Predecessors": ", ".join(predecessors),
            "is blocked by": ", ".join(predecessors),
        })

    return buffer.getvalue()


def _description(task):
    """Short context Jira users see, including the PERT code for traceability."""
    parts = [f"PERT task {task.code}"]
    if task.start_date and task.end_date:
        parts.append(
            f"scheduled {task.start_date.isoformat()} to {task.end_date.isoformat()}"
        )
    if task.responsible:
        parts.append(f"responsible: {task.responsible}")
    return ". ".join(parts) + "."


def export_filename(project):
    """Download filename for the export (keeps it filesystem-safe)."""
    safe = "".join(c if c.isalnum() or c in " -_" else "-" for c in project.name)
    safe = "-".join(safe.split()) or "project"
    return f"{safe}-jira-import.csv"
