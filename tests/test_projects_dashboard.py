"""Tests for the Projects Dashboard (portfolio home page)."""

from datetime import date
import pytest

from app import create_app
from app.db import db
from app.models import Project, Lane, Task, TASK_KIND_MILESTONE


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


def test_dashboard_empty_state(client, app):
    resp = client.get("/")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "Projects Dashboard" in html
    assert "Welcome to Projects Dashboard" in html
    assert "Create your first project" in html


def test_dashboard_with_projects(client, app):
    with app.app_context():
        p1 = Project(name="E-Commerce Redesign", description="Rebuilding the shopping cart and checkout.")
        db.session.add(p1)
        db.session.flush()

        lane1 = Lane(project_id=p1.id, name="Frontend", sort_order=1)
        db.session.add(lane1)
        db.session.flush()

        t1 = Task(
            project_id=p1.id,
            code="001",
            lane_id=lane1.id,
            name="Cart UI",
            start_date=date(2026, 11, 10),
            duration_days=5,
            started=True,
            done=False,
        )
        m1 = Task(
            project_id=p1.id,
            code="010",
            kind=TASK_KIND_MILESTONE,
            lane_id=lane1.id,
            name="Beta Release",
            start_date=date(2026, 11, 20),
            duration_days=0,
            baseline_start_date=date(2026, 11, 18),
        )
        db.session.add_all([t1, m1])
        db.session.commit()
        pid = p1.id

    resp = client.get("/")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)

    # Header and KPI section
    assert "Projects Dashboard" in html
    assert "1 Active" in html
    assert "Portfolio Progress" in html
    assert "Milestone Gates" in html

    # Project card content
    assert "E-Commerce Redesign" in html
    assert "Rebuilding the shopping cart" in html
    assert "Beta Release" in html
    assert "+2d delay vs baseline" in html
    assert "Open &rarr;" in html


def test_create_project_and_rename_delete(client, app):
    # 1. Create project via POST
    resp = client.post("/projects", data={
        "name": "Mobile App V2",
        "description": "Next generation iOS and Android client",
    })
    assert resp.status_code == 302

    with app.app_context():
        proj = Project.query.filter_by(name="Mobile App V2").first()
        assert proj is not None
        assert proj.description == "Next generation iOS and Android client"
        pid = proj.id

    # Check dashboard now shows the new project
    dash = client.get("/")
    assert "Mobile App V2" in dash.get_data(as_text=True)

    # 2. Rename project
    rename_resp = client.post(f"/projects/{pid}/rename", data={"name": "Mobile App Global"})
    assert rename_resp.status_code == 302
    with app.app_context():
        assert Project.query.get(pid).name == "Mobile App Global"

    # 3. Delete project
    del_resp = client.post(f"/projects/{pid}/delete")
    assert del_resp.status_code == 302
    with app.app_context():
        assert Project.query.get(pid) is None
