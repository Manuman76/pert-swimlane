"""Automatic requirement coverage from linked tasks."""

from datetime import date

import pytest

from app import create_app
from app.db import db
from app.models import Project, Lane, Task, Requirement
from app.requirement_trace import (
    REQ_COVERAGE_IMPLEMENTED,
    REQ_COVERAGE_IN_PROGRESS,
    REQ_COVERAGE_NOT_COVERED,
    REQ_COVERAGE_PLANNED,
    compute_coverage,
    coverage_status_display,
    task_codes_display,
)


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


def _task(project, lane, code, **kwargs):
    t = Task(
        project_id=project.id,
        code=code,
        lane_id=lane.id,
        name=kwargs.get("name", code),
        start_date=kwargs.get("start_date", date(2017, 6, 1)),
        duration_days=kwargs.get("duration_days", 1),
        started=kwargs.get("started", False),
        done=kwargs.get("done", False),
    )
    db.session.add(t)
    return t


@pytest.fixture
def client(app):
    return app.test_client()


def test_coverage_not_covered_without_tasks(app):
    with app.app_context():
        assert compute_coverage([]) == REQ_COVERAGE_NOT_COVERED
        assert task_codes_display([]) == "none"
        assert coverage_status_display(REQ_COVERAGE_NOT_COVERED, []) == "\u2014"


def test_coverage_planned_when_all_not_started(app):
    with app.app_context():
        proj = Project(name="P")
        db.session.add(proj)
        db.session.flush()
        lane = Lane(project_id=proj.id, name="L", sort_order=1)
        db.session.add(lane)
        db.session.flush()
        t = _task(proj, lane, "002")
        assert compute_coverage([t]) == REQ_COVERAGE_PLANNED


def test_coverage_in_progress_when_started(app):
    with app.app_context():
        proj = Project(name="P")
        db.session.add(proj)
        db.session.flush()
        lane = Lane(project_id=proj.id, name="L", sort_order=1)
        db.session.add(lane)
        db.session.flush()
        t = _task(proj, lane, "003", started=True)
        assert compute_coverage([t]) == REQ_COVERAGE_IN_PROGRESS
        assert coverage_status_display(REQ_COVERAGE_IN_PROGRESS, [t]) == "Started"


def test_coverage_implemented_when_all_done(app):
    with app.app_context():
        proj = Project(name="P")
        db.session.add(proj)
        db.session.flush()
        lane = Lane(project_id=proj.id, name="L", sort_order=1)
        db.session.add(lane)
        db.session.flush()
        t1 = _task(proj, lane, "002", done=True)
        t2 = _task(proj, lane, "005", done=True)
        assert compute_coverage([t1, t2]) == REQ_COVERAGE_IMPLEMENTED
        assert task_codes_display([t1, t2]) == "002, 005"


def test_requirements_matrix_in_ui(client, app):
    with app.app_context():
        proj = Project(name="Trace")
        db.session.add(proj)
        db.session.flush()
        lane = Lane(project_id=proj.id, name="Dev", sort_order=1)
        db.session.add(lane)
        db.session.flush()
        req = Requirement(
            project_id=proj.id, code="BR-001", title="Audit", moscow="must",
        )
        t = _task(proj, lane, "002", started=True)
        req.tasks = [t]
        db.session.add(req)
        db.session.commit()
        pid = proj.id

    html = client.get(f"/projects/{pid}/requirements/list").get_data(as_text=True)
    assert "BR-001" in html
    assert "002" in html
    assert "Started" in html
    assert "\U0001f7e6" in html  # in progress
    assert "requirement-done-check" not in html
