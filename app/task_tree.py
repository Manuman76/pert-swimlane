"""Order tasks for the grid as an expandable tree from predecessor links.

Each task is shown once. When it has several predecessors, it is nested under
the lowest predecessor code (stable tie-break). Cycles and unreachable tasks are
listed after the forest at the top level.
"""

from collections import defaultdict
from dataclasses import dataclass


@dataclass(frozen=True)
class TaskTreeRow:
    task: object
    depth: int
    ancestors: tuple
    has_children: bool
    primary_parent: str | None


def _sort_key(task):
    return (task.start_date, task.code)


def build_task_tree_rows(tasks):
    """Return ``TaskTreeRow`` instances in depth-first display order."""
    if not tasks:
        return []

    by_code = {t.code: t for t in tasks}
    primary_parent = {}
    for task in tasks:
        preds = [p for p in task.predecessor_codes() if p in by_code]
        if preds:
            primary_parent[task.code] = min(preds)

    children = defaultdict(list)
    for code, parent_code in primary_parent.items():
        children[parent_code].append(by_code[code])

    for parent_code in children:
        children[parent_code].sort(key=_sort_key)

    roots = [t for t in tasks if t.code not in primary_parent]
    roots.sort(key=_sort_key)

    rows = []
    visited = set()

    def walk(task, depth, ancestors):
        if task.code in visited:
            return
        visited.add(task.code)
        kids = children.get(task.code, [])
        rows.append(
            TaskTreeRow(
                task=task,
                depth=depth,
                ancestors=ancestors,
                has_children=bool(kids),
                primary_parent=primary_parent.get(task.code),
            )
        )
        for child in kids:
            walk(child, depth + 1, ancestors + (task.code,))

    for root in roots:
        walk(root, 0, ())

    remaining = sorted(
        (t for t in tasks if t.code not in visited),
        key=_sort_key,
    )
    for task in remaining:
        walk(task, 0, ())

    return rows
