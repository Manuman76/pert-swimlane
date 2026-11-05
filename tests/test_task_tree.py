from datetime import date

from app.models import Project, Lane, Task
from app.task_tree import build_task_tree_rows


def _task(project, lane, code, name, preds=None, start=None):
    return Task(
        project_id=project.id,
        code=code,
        lane_id=lane.id,
        name=name,
        start_date=start or date(2024, 1, 1),
        duration_days=1,
        predecessors=preds,
    )


def test_task_tree_roots_then_children():
    proj = Project(name="P")
    lane = Lane(project_id=1, name="L", sort_order=1)
    proj.id = lane.project_id = 1
    lane.id = 1
    t1 = _task(proj, lane, "001", "Root")
    t2 = _task(proj, lane, "002", "Child", preds="001")
    t3 = _task(proj, lane, "003", "Grandchild", preds="002")
    rows = build_task_tree_rows([t2, t3, t1])
    assert [r.task.code for r in rows] == ["001", "002", "003"]
    assert [r.depth for r in rows] == [0, 1, 2]
    assert rows[1].ancestors == ("001",)
    assert rows[2].ancestors == ("001", "002")
    assert rows[0].has_children is True
    assert rows[2].has_children is False


def test_task_tree_multi_predecessor_uses_lowest_code():
    proj = Project(name="P")
    lane = Lane(project_id=1, name="L", sort_order=1)
    proj.id = lane.project_id = lane.id = 1
    t1 = _task(proj, lane, "001", "A")
    t2 = _task(proj, lane, "002", "B")
    t3 = _task(proj, lane, "003", "Join", preds="002, 001")
    rows = build_task_tree_rows([t1, t2, t3])
    assert [r.task.code for r in rows] == ["001", "003", "002"]
    join = next(r for r in rows if r.task.code == "003")
    assert join.depth == 1
    assert join.primary_parent == "001"


def test_task_tree_cycle_tasks_still_listed():
    proj = Project(name="P")
    lane = Lane(project_id=1, name="L", sort_order=1)
    proj.id = lane.project_id = lane.id = 1
    t1 = _task(proj, lane, "001", "A", preds="002")
    t2 = _task(proj, lane, "002", "B", preds="001")
    rows = build_task_tree_rows([t1, t2])
    assert {r.task.code for r in rows} == {"001", "002"}
    assert len(rows) == 2
