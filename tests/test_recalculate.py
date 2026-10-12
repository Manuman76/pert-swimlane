"""Tests for the date-recomputation behaviour.

The grid is the source of truth: whenever a task (start date, duration,
predecessors) or a whole task changes, :func:`app.routes.recalculate_project`
pushes every dependent task forward along the dependency chain so that
``successor.start >= max(pred.end)`` always holds, and every task's
``end_date`` remains ``start_date + duration_days``. Recalculation only
ever moves a date later, never earlier.
"""
from datetime import date

import pytest

from app import create_app
from app.db import db
from app.models import Lane, Project, Task
from app.routes import recalculate_project, project_date_stats


@pytest.fixture
def app():
    app = create_app({
        "TESTING": True,
        "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
    })
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()


def _new_project(name, lane_names=("L1",)):
    proj = Project(name=name)
    db.session.add(proj)
    db.session.commit()
    lanes = []
    for i, n in enumerate(lane_names, 1):
        lanes.append(Lane(project_id=proj.id, name=n, sort_order=i))
    db.session.add_all(lanes)
    db.session.commit()
    return proj, lanes


def _add_task(proj, lane, code, name, start, dur, preds=None):
    t = Task(
        project_id=proj.id,
        code=code,
        lane_id=lane.id,
        name=name,
        start_date=start,
        duration_days=dur,
        predecessors=preds,
    )
    db.session.add(t)
    db.session.commit()
    return t


def _task(proj, code):
    return Task.query.filter_by(project_id=proj.id, code=code).one()


# ---------------------------------------------------------------------------
# recalculate_project (direct, no HTTP)
# ---------------------------------------------------------------------------

def test_recalc_pushes_successor_after_predecessor(app):
    proj, (lane,) = _new_project("R1")
    _add_task(proj, lane, "001", "A", date(2017, 6, 1), 3)
    _add_task(proj, lane, "002", "B", date(2017, 6, 1), 8, "001")  # violates the rule

    recalculate_project(proj.id)
    db.session.commit()

    t1, t2 = _task(proj, "001"), _task(proj, "002")
    assert t1.start_date == date(2017, 6, 1)      # anchor untouched
    assert t1.end_date == date(2017, 6, 4)
    assert t2.start_date == date(2017, 6, 4)      # 001's exclusive end
    assert t2.end_date == date(2017, 6, 12)


def test_recalc_does_not_move_successor_earlier(app):
    proj, (lane,) = _new_project("R2")
    _add_task(proj, lane, "001", "A", date(2017, 6, 1), 2)
    _add_task(proj, lane, "002", "B", date(2017, 6, 10), 1, "001")

    recalculate_project(proj.id)
    db.session.commit()

    assert _task(proj, "002").start_date == date(2017, 6, 10)


def test_recalc_cascades_through_chain(app):
    proj, (lane,) = _new_project("R3")
    _add_task(proj, lane, "001", "A", date(2017, 6, 1), 3)
    _add_task(proj, lane, "002", "B", date(2017, 6, 2), 2, "001")  # invalid
    _add_task(proj, lane, "003", "C", date(2017, 6, 4), 4, "002")

    moved = recalculate_project(proj.id)
    db.session.commit()

    assert _task(proj, "001").start_date == date(2017, 6, 1)
    assert _task(proj, "002").start_date == date(2017, 6, 4)  # = 001's exclusive end
    assert _task(proj, "003").start_date == date(2017, 6, 6)  # = 002's exclusive end
    assert set(moved) == {"002", "003"}


def test_recalc_joins_multiple_predecessors(app):
    proj, (lane,) = _new_project("R4")
    _add_task(proj, lane, "001", "A", date(2017, 6, 1), 3)
    _add_task(proj, lane, "002", "B", date(2017, 6, 1), 5)
    _add_task(proj, lane, "003", "C", date(2017, 6, 5), 2, "001,002")  # 002 ends 6

    recalculate_project(proj.id)
    db.session.commit()

    assert _task(proj, "003").start_date == date(2017, 6, 6)
    assert _task(proj, "003").end_date == date(2017, 6, 8)


def test_recalc_ignores_unknown_predecessor_codes(app):
    proj, (lane,) = _new_project("R5")
    _add_task(proj, lane, "001", "A", date(2017, 6, 1), 3)
    _add_task(proj, lane, "002", "B", date(2017, 6, 10), 2, "999")

    moved = recalculate_project(proj.id)
    db.session.commit()

    assert _task(proj, "002").start_date == date(2017, 6, 10)
    assert moved == []


def test_recalc_safe_on_predecessor_cycle(app):
    """A cycle (e.g. from corrupted data) must not loop forever and must
    leave the tasks untouched."""
    proj, (lane,) = _new_project("R6")
    _add_task(proj, lane, "001", "A", date(2017, 6, 1), 2, "002")
    _add_task(proj, lane, "002", "B", date(2017, 6, 3), 2, "001")

    recalculate_project(proj.id)
    db.session.commit()

    assert _task(proj, "001").start_date == date(2017, 6, 1)
    assert _task(proj, "002").start_date == date(2017, 6, 3)


def test_recalc_no_tasks_is_noop(app):
    proj, _ = _new_project("R7")
    assert recalculate_project(proj.id) == []
    assert recalculate_project(99999) == []


# ---------------------------------------------------------------------------
# HTTP routes: the recalculation happens AND is persisted
# ---------------------------------------------------------------------------

def test_update_route_persists_pushed_dates(client, app):
    with app.app_context():
        proj, (lane,) = _new_project("U1")
        t1 = _add_task(proj, lane, "001", "A", date(2017, 6, 1), 3)
        _add_task(proj, lane, "002", "B", date(2017, 6, 5), 2, "001")
        t1_id = t1.id

        # Extend A from 3 to 6 days: B must move from Jun 5 to Jun 7.
        resp = client.post(f"/tasks/{t1_id}/update", data={
            "name": "A",
            "lane_id": str(lane.id),
            "start_date": "1 Jun 2017",
            "duration_days": "6",
            "responsible": "",
            "predecessors": "",
        })
        assert resp.status_code == 200

        db.session.expire_all()
        b = Task.query.filter_by(project_id=proj.id, code="002").one()
        assert b.start_date == date(2017, 6, 7)
        assert b.end_date == date(2017, 6, 9)

        # The refreshed grid shows the new dates for both tasks.
        html = resp.get_data(as_text=True)
        assert "7 Jun 2017" in html        # new start of B
        assert "9 Jun 2017" in html        # new end of B
        assert "7 Jun 2017" in html        # new end of A


def test_update_route_persists_new_predecessor(client, app):
    with app.app_context():
        proj, (lane,) = _new_project("U2")
        _add_task(proj, lane, "001", "A", date(2017, 6, 1), 3)
        _add_task(proj, lane, "002", "B", date(2017, 6, 10), 1, "001")
        t3 = _add_task(proj, lane, "003", "C", date(2017, 6, 5), 2)

        # Make C depend on B. B ends 2017-06-11 (exclusive), so C must be
        # pushed from Jun 5 to Jun 11.
        resp = client.post(f"/tasks/{t3.id}/update", data={
            "name": "C",
            "lane_id": str(lane.id),
            "start_date": "5 Jun 2017",
            "duration_days": "2",
            "responsible": "",
            "predecessors": "002",
        })
        assert resp.status_code == 200

        db.session.expire_all()
        c = Task.query.filter_by(project_id=proj.id, code="003").one()
        assert c.predecessors == "002"
        assert c.start_date == date(2017, 6, 11)


def test_update_route_is_push_only(client, app):
    with app.app_context():
        proj, (lane,) = _new_project("U3")
        _add_task(proj, lane, "001", "A", date(2017, 6, 1), 3)
        _add_task(proj, lane, "002", "B", date(2017, 6, 10), 1, "001")
        t3 = _add_task(proj, lane, "003", "C", date(2017, 6, 30), 2)

        # C is already scheduled well after B ends: adding the dependency
        # must not pull C earlier.
        resp = client.post(f"/tasks/{t3.id}/update", data={
            "name": "C",
            "lane_id": str(lane.id),
            "start_date": "30 Jun 2017",
            "duration_days": "2",
            "responsible": "",
            "predecessors": "002",
        })
        assert resp.status_code == 200

        db.session.expire_all()
        c = Task.query.filter_by(project_id=proj.id, code="003").one()
        assert c.start_date == date(2017, 6, 30)
        assert c.end_date == date(2017, 7, 2)


def test_update_route_rejects_start_before_predecessor(client, app):
    with app.app_context():
        proj, (lane,) = _new_project("U4")
        _add_task(proj, lane, "001", "A", date(2017, 6, 1), 3)
        t2 = _add_task(proj, lane, "002", "B", date(2017, 6, 5), 2, "001")

        # Trying to move B earlier than A finishes is a scheduling error.
        resp = client.post(f"/tasks/{t2.id}/update", data={
            "name": "B",
            "lane_id": str(lane.id),
            "start_date": "2 Jun 2017",
            "duration_days": "2",
            "responsible": "",
            "predecessors": "001",
        })
        assert resp.status_code == 422

        db.session.expire_all()
        assert Task.query.filter_by(project_id=proj.id, code="002").one().start_date == date(2017, 6, 5)


def test_update_route_rejects_unknown_predecessor(client, app):
    with app.app_context():
        proj, (lane,) = _new_project("U5")
        _add_task(proj, lane, "001", "A", date(2017, 6, 1), 3)
        t2 = _add_task(proj, lane, "002", "B", date(2017, 6, 5), 2)

        resp = client.post(f"/tasks/{t2.id}/update", data={
            "name": "B",
            "lane_id": str(lane.id),
            "start_date": "5 Jun 2017",
            "duration_days": "2",
            "responsible": "",
            "predecessors": "999",
        })
        assert resp.status_code == 422

        db.session.expire_all()
        assert Task.query.filter_by(project_id=proj.id, code="002").one().predecessors is None


def test_create_route_persists_new_task(client, app):
    with app.app_context():
        proj, (lane,) = _new_project("U6")
        before = Task.query.filter_by(project_id=proj.id).count()

        resp = client.post(f"/projects/{proj.id}/tasks", data={"lane_id": str(lane.id)})
        assert resp.status_code == 200
        assert Task.query.filter_by(project_id=proj.id).count() == before + 1


def test_delete_route_removes_task_and_keeps_chain_consistent(client, app):
    with app.app_context():
        proj, (lane,) = _new_project("D1")
        _add_task(proj, lane, "001", "A", date(2017, 6, 1), 3)
        t2 = _add_task(proj, lane, "002", "B", date(2017, 6, 5), 2, "001")
        t2_id = t2.id

        resp = client.post(f"/tasks/{t2_id}/delete")
        assert resp.status_code == 200

        db.session.expire_all()
        remaining = Task.query.filter_by(project_id=proj.id).all()
        assert [t.code for t in remaining] == ["001"]
        assert remaining[0].start_date == date(2017, 6, 1)
        assert remaining[0].end_date == date(2017, 6, 4)


# ---------------------------------------------------------------------------
# project_date_stats
# ---------------------------------------------------------------------------

def test_project_date_stats_empty(app):
    with app.app_context():
        proj, _ = _new_project("E1")
        tasks = Task.query.filter_by(project_id=proj.id).all()
        assert project_date_stats(tasks) == (0, 0, None, None)


def test_project_date_stats_totals(app):
    with app.app_context():
        proj, (lane,) = _new_project("E2")
        _add_task(proj, lane, "001", "A", date(2017, 6, 1), 3)
        _add_task(proj, lane, "002", "B", date(2017, 6, 5), 2, "001")
        # Parallel siblings: both start 2017-06-14, so effort adds up
        _add_task(proj, lane, "003", "C", date(2017, 6, 14), 8)
        _add_task(proj, lane, "004", "D", date(2017, 6, 14), 8)

        total_effort, total_duration, start, end = project_date_stats(
            Task.query.filter_by(project_id=proj.id).all())

        assert total_effort == 3 + 2 + 8 + 8
        assert start == date(2017, 6, 1)
        assert end == date(2017, 6, 22)
        assert total_duration == (end - start).days
