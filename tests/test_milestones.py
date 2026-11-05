"""Milestone tasks: zero duration, target date, diagram diamonds."""

from datetime import date

import pytest

from app import create_app
from app.db import db
from app.jira_export import export_project_csv
from app.models import TASK_KIND_MILESTONE, Task
from tests.test_app import _create_lane, _create_project


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


def test_milestone_end_date_is_target(app):
    with app.app_context():
        proj = _create_project("MS1")
        lane = _create_lane(proj, "Delivery", sort_order=1)
        ms = Task(
            project_id=proj.id,
            code="001",
            kind=TASK_KIND_MILESTONE,
            lane_id=lane.id,
            name="Go-Live",
            start_date=date(2026, 3, 15),
            duration_days=0,
        )
        db.session.add(ms)
        db.session.commit()
        assert ms.is_milestone
        assert ms.end_date == date(2026, 3, 15)


def test_create_milestone_via_post(client, app):
    with app.app_context():
        proj = _create_project("MS2")
        _create_lane(proj, "Lane 1", sort_order=1)
        pid = proj.id

    resp = client.post(
        f"/projects/{pid}/tasks",
        data={"kind": "milestone"},
        headers={"HX-Request": "true"},
    )
    assert resp.status_code == 200
    assert "task-row-milestone" in resp.get_data(as_text=True)
    assert "◆" in resp.get_data(as_text=True)

    with app.app_context():
        task = Task.query.filter_by(project_id=pid).one()
        assert task.kind == TASK_KIND_MILESTONE
        assert task.duration_days == 0
        assert task.name == "New Milestone"


def test_diagram_renders_milestone_diamond(app):
    with app.app_context():
        from app.diagram import build_diagram

        proj = _create_project("MS3")
        lane = _create_lane(proj, "Work", sort_order=1)
        db.session.add(
            Task(
                project_id=proj.id,
                code="001",
                kind=TASK_KIND_MILESTONE,
                lane_id=lane.id,
                name="Architecture Approved",
                start_date=date(2026, 1, 10),
                duration_days=0,
            )
        )
        db.session.commit()
        tasks = Task.query.filter_by(project_id=proj.id).all()
        html = build_diagram([lane], tasks)
        assert 'class="milestone-node' in html
        assert "milestone-diamond" in html
        assert "Architecture Approved" in html


def test_jira_export_milestone_issue_type(app):
    with app.app_context():
        proj = _create_project("MS4")
        lane = _create_lane(proj, "Work", sort_order=1)
        db.session.add(
            Task(
                project_id=proj.id,
                code="001",
                kind=TASK_KIND_MILESTONE,
                lane_id=lane.id,
                name="SIT Complete",
                start_date=date(2026, 2, 1),
                duration_days=0,
            )
        )
        db.session.commit()
        lanes = [lane]
        tasks = Task.query.filter_by(project_id=proj.id).all()
        csv_text = export_project_csv(proj, lanes, tasks)
        assert "Milestone" in csv_text
        assert ",0," in csv_text or '",0,' in csv_text


def test_validate_milestone_rejects_nonzero_duration(app):
    with app.app_context():
        from app.routes import validate_task

        proj = _create_project("MS5")
        lane = _create_lane(proj, "Work", sort_order=1)
        bad = Task(
            project_id=proj.id,
            code="001",
            kind=TASK_KIND_MILESTONE,
            lane_id=lane.id,
            name="Bad",
            start_date=date(2026, 1, 1),
            duration_days=3,
        )
        errors = validate_task(bad, proj.id)
        assert any("zero duration" in e.lower() for e in errors)
