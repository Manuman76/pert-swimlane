import pytest
from datetime import date, timedelta
from app import create_app
from app.db import db
from app.models import Project, Lane, Task


@pytest.fixture
def app():
    app = create_app()
    app.config.update({
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


def _create_project(name):
    proj = Project(name=name)
    db.session.add(proj)
    db.session.commit()
    return proj


def _create_lane(proj, name, sort_order=1):
    lane = Lane(project_id=proj.id, name=name, sort_order=sort_order)
    db.session.add(lane)
    db.session.commit()
    return lane


# Unique project name counter for test isolation
_project_counter = 0


def test_end_date(app):
    with app.app_context():
        proj = _create_project("TestEndDate")
        lane = _create_lane(proj, "Lane1")
        task = Task(
            project_id=proj.id,
            code="001",
            lane_id=lane.id,
            name="T1",
            start_date=date(2017, 6, 1),
            duration_days=3,
        )
        assert task.end_date == date(2017, 6, 4)


def test_code_auto_assignment(client, app):
    with app.app_context():
        proj = _create_project("P1")
        lane = _create_lane(proj, "L")

        resp1 = client.post(f"/projects/{proj.id}/tasks", data={"lane_id": lane.id})
        assert resp1.status_code == 200
        resp2 = client.post(f"/projects/{proj.id}/tasks", data={"lane_id": lane.id})
        assert resp2.status_code == 200

        tasks = Task.query.filter_by(project_id=proj.id).order_by(Task.code).all()
        assert [t.code for t in tasks] == ["001", "002"]


def test_validation_unknown_predecessor(client, app):
    with app.app_context():
        proj = _create_project("P2")
        lane = _create_lane(proj, "L")
        task = Task(
            project_id=proj.id,
            code="001",
            lane_id=lane.id,
            name="T1",
            start_date=date(2017, 6, 1),
            duration_days=3,
        )
        db.session.add(task)
        db.session.commit()

        resp = client.post(f"/tasks/{task.id}/update", data={
            "predecessors": "999",
            "start_date": "1 Jun 2017",
            "duration_days": "3",
        })
        assert resp.status_code == 422


def test_validation_cycle(client, app):
    with app.app_context():
        proj = _create_project("P3")
        lane = _create_lane(proj, "L")
        t1 = Task(project_id=proj.id, code="001", lane_id=lane.id, name="T1", start_date=date(2017, 6, 1), duration_days=3)
        t2 = Task(project_id=proj.id, code="002", lane_id=lane.id, name="T2", start_date=date(2017, 6, 5), duration_days=3)
        db.session.add_all([t1, t2])
        db.session.commit()

        # Set 002's predecessor to 001 to form a potential cycle
        t2.predecessors = "001"
        db.session.commit()

        # Now 001 -> 002 would create a cycle: 001 -> 002 -> 001
        resp = client.post(f"/tasks/{t1.id}/update", data={
            "predecessors": "002",
            "start_date": "10 Jun 2017",
            "duration_days": "3",
        })
        assert resp.status_code == 422


def test_validation_scheduling(client, app):
    with app.app_context():
        proj = _create_project("P4")
        lane = _create_lane(proj, "L")
        t1 = Task(project_id=proj.id, code="001", lane_id=lane.id, name="T1", start_date=date(2017, 6, 1), duration_days=3)
        db.session.add(t1)
        db.session.commit()

        t2 = Task(project_id=proj.id, code="002", lane_id=lane.id, name="T2", start_date=date(2017, 6, 5), duration_days=1)
        db.session.add(t2)
        db.session.commit()

        # T2 starts before T1 ends (T1 ends 2017-06-04, T2 starts 2017-06-02)
        resp = client.post(f"/tasks/{t2.id}/update", data={
            "predecessors": "001",
            "start_date": "2 Jun 2017",
            "duration_days": "1",
        })
        assert resp.status_code == 422


def test_layout_coordinates(app):
    with app.app_context():
        proj = _create_project("P5")
        lane = _create_lane(proj, "L")
        task = Task(project_id=proj.id, code="001", lane_id=lane.id, name="T1", start_date=date(2017, 6, 1), duration_days=3)
        db.session.add(task)
        db.session.commit()

        from app.diagram import build_diagram
        html = build_diagram([lane], [task])
        assert "task-card" in html
        assert "001" in html
