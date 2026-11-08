"""Project Overview dashboard metrics."""

from datetime import date

import pytest

from app import create_app
from app.db import db
from app.models import (
    Project,
    Lane,
    Task,
    Requirement,
    RaidItem,
    TASK_KIND_MILESTONE,
    RAID_KIND_RISK,
)
from app.project_overview import build_project_overview


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


def test_overview_tab_default(client, app):
    with app.app_context():
        proj = Project(name="demo project")
        db.session.add(proj)
        db.session.commit()
        pid = proj.id

    html = client.get(f"/projects/{pid}").get_data(as_text=True)
    assert 'id="overview-tab"' in html
    assert "overview-pane" in html
    assert "show active" in html.split("overview-pane")[0][-80:] or 'id="overview-pane"' in html
    assert 'id="overview-pane"' in html
    assert 'id="overview-pane" role="tabpanel"' in html or "overview-pane" in html
    # Overview is active; tasks pane is not the active tab pane
    assert html.index("overview-pane") < html.index("tasks-pane")


def test_task_progress_counts(app):
    with app.app_context():
        proj = Project(name="P")
        db.session.add(proj)
        db.session.flush()
        lane = Lane(project_id=proj.id, name="L", sort_order=1)
        db.session.add(lane)
        db.session.flush()
        for code, done, started in [
            ("001", True, True),
            ("002", True, True),
            ("003", False, True),
            ("004", False, False),
        ]:
            db.session.add(Task(
                project_id=proj.id, code=code, lane_id=lane.id, name=code,
                start_date=date(2026, 10, 20), duration_days=2,
                done=done, started=started,
            ))
        db.session.commit()
        tasks = Task.query.filter_by(project_id=proj.id).all()
        overview = build_project_overview(tasks, [], [])
        assert overview.tasks_total == 4
        assert overview.tasks_completed == 2
        assert overview.tasks_in_progress == 1
        assert overview.tasks_not_started == 1
        assert overview.progress_percent == 50


def test_requirement_coverage_counts(app):
    with app.app_context():
        proj = Project(name="P")
        db.session.add(proj)
        db.session.flush()
        lane = Lane(project_id=proj.id, name="L", sort_order=1)
        db.session.add(lane)
        db.session.flush()
        r1 = Requirement(project_id=proj.id, code="BR-001", title="A", moscow="must")
        r2 = Requirement(project_id=proj.id, code="BR-002", title="B", moscow="must")
        t = Task(
            project_id=proj.id, code="001", lane_id=lane.id, name="Work",
            start_date=date(2026, 10, 1), duration_days=1,
        )
        r1.tasks = [t]
        db.session.add_all([r1, r2, t])
        db.session.commit()
        reqs = Requirement.query.filter_by(project_id=proj.id).all()
        overview = build_project_overview([t], reqs, [])
        assert overview.requirements_total == 2
        assert overview.requirements_covered == 1
        assert overview.requirements_uncovered == 1


def test_risk_tiers_and_milestone(app):
    with app.app_context():
        proj = Project(name="P")
        db.session.add(proj)
        db.session.flush()
        lane = Lane(project_id=proj.id, name="L", sort_order=1)
        db.session.add(lane)
        db.session.flush()
        ms = Task(
            project_id=proj.id, code="010", lane_id=lane.id, name="SIT Ready",
            kind=TASK_KIND_MILESTONE, start_date=date(2026, 11, 8), duration_days=0,
        )
        db.session.add(ms)
        db.session.add(RaidItem(
            project_id=proj.id, kind=RAID_KIND_RISK, code="R-001",
            title="Firewall", probability="high", impact="high", status="open",
        ))
        db.session.add(RaidItem(
            project_id=proj.id, kind=RAID_KIND_RISK, code="R-002",
            title="Vendor", probability="high", impact="medium", status="open",
        ))
        db.session.commit()
        tasks = Task.query.filter_by(project_id=proj.id).all()
        risks = RaidItem.query.filter_by(project_id=proj.id).all()
        overview = build_project_overview(tasks, [], risks)
        assert overview.risk_critical == 1
        assert overview.risk_high == 1
        assert overview.next_milestone.name == "SIT Ready"
        assert overview.milestones_total == 1
        assert overview.milestones_completed == 0
        assert overview.health_status == "critical"
        assert overview.health_label == "Needs Attention"


def test_overview_partial_endpoint(client, app):
    with app.app_context():
        proj = Project(name="Steering")
        db.session.add(proj)
        db.session.commit()
        pid = proj.id

    resp = client.get(f"/projects/{pid}/overview")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "Progress" in html
    assert "overview-hero" in html
    assert "Requirements" in html
    assert "Milestones" in html


def test_overview_shows_baseline_and_variance(client, app):
    with app.app_context():
        proj = Project(name="Steering Variance")
        db.session.add(proj)
        db.session.flush()
        lane = Lane(project_id=proj.id, name="Gates", sort_order=1)
        db.session.add(lane)
        db.session.flush()
        m = Task(
            project_id=proj.id,
            code="M01",
            kind=TASK_KIND_MILESTONE,
            lane_id=lane.id,
            name="SIT Ready",
            start_date=date(2026, 11, 14),
            duration_days=0,
            baseline_start_date=date(2026, 11, 12),
        )
        db.session.add(m)
        db.session.commit()
        pid = proj.id

    resp = client.get(f"/projects/{pid}/overview")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "Schedule variance" in html
    assert "+2d" in html
    assert "SIT Ready" in html


def test_overdue_project_health_status(app):
    """When a project's scheduled finish date has passed with incomplete tasks,
    it should not appear 'On Track'."""
    from datetime import timedelta

    today = date.today()
    past_date = today - timedelta(days=10)

    # 1. Project past finish date with incomplete tasks -> Needs Attention (critical)
    t_overdue = Task(
        code="001",
        name="Past Work",
        start_date=past_date,
        duration_days=2,
        started=False,
        done=False,
    )
    overview_crit = build_project_overview([t_overdue], [], [])
    assert overview_crit.health_status == "critical"
    assert overview_crit.health_label == "Needs Attention"
    assert "finish date passed" in overview_crit.health_summary

    # 2. When all tasks are completed, even if in past -> Complete
    t_done = Task(
        code="001",
        name="Past Work Done",
        start_date=past_date,
        duration_days=2,
        started=True,
        done=True,
    )
    overview_done = build_project_overview([t_done], [], [])
    assert overview_done.health_status == "completed"
    assert overview_done.health_label == "Complete"

    # 3. Future task not started -> On Track
    future_date = today + timedelta(days=10)
    t_future = Task(
        code="001",
        name="Future Work",
        start_date=future_date,
        duration_days=2,
        started=False,
        done=False,
    )
    overview_future = build_project_overview([t_future], [], [])
    assert overview_future.health_status == "on_track"
    assert overview_future.health_label == "On Track"


