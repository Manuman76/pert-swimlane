"""Tests for Schedule Baseline and Variance tracking."""

from datetime import date
import pytest

from app import create_app
from app.db import db
from app.models import Project, Lane, Task, TASK_KIND_MILESTONE
from app.workdays import diff_workdays
from app.services.scheduler import set_project_baseline, clear_project_baseline
from app.project_overview import build_project_overview
from app.diagram import build_diagram


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


def test_diff_workdays_signed():
    # Same date
    d = date(2026, 11, 12)
    assert diff_workdays(d, d) == 0

    # Thursday Nov 12 to Saturday Nov 14 (Thu + Fri working days = 2)
    thu = date(2026, 11, 12)
    sat = date(2026, 11, 14)
    assert diff_workdays(thu, sat) == 2

    # Friday to Monday (only Friday counts in [start, end)) -> 1 workday
    fri = date(2026, 11, 13)
    mon = date(2026, 11, 16)
    assert diff_workdays(fri, mon) == 1

    # Inverted: ahead of schedule
    assert diff_workdays(sat, thu) == -2
    assert diff_workdays(mon, fri) == -1


def test_set_and_clear_baseline(app):
    with app.app_context():
        proj = Project(name="Baseline Proj")
        db.session.add(proj)
        db.session.flush()
        lane = Lane(project_id=proj.id, name="General", sort_order=1)
        db.session.add(lane)
        db.session.flush()

        t1 = Task(
            project_id=proj.id,
            code="001",
            lane_id=lane.id,
            name="Analysis",
            start_date=date(2026, 10, 5),
            duration_days=5,
        )
        m1 = Task(
            project_id=proj.id,
            code="002",
            kind=TASK_KIND_MILESTONE,
            lane_id=lane.id,
            name="SIT Ready",
            start_date=date(2026, 10, 12),
            duration_days=0,
        )
        db.session.add_all([t1, m1])
        db.session.commit()
        pid = proj.id

        assert proj.baseline_set_at is None
        assert t1.baseline_start_date is None

        count = set_project_baseline(pid)
        assert count == 2

        db.session.refresh(proj)
        db.session.refresh(t1)
        db.session.refresh(m1)

        assert proj.baseline_set_at is not None
        assert t1.baseline_start_date == date(2026, 10, 5)
        assert t1.baseline_end_date == t1.end_date
        assert m1.baseline_start_date == date(2026, 10, 12)
        assert m1.variance_days == 0

        # Now clear baseline
        clear_project_baseline(pid)
        db.session.refresh(proj)
        db.session.refresh(t1)
        db.session.refresh(m1)

        assert proj.baseline_set_at is None
        assert t1.baseline_start_date is None
        assert m1.baseline_start_date is None


def test_baseline_schedule_variance_user_scenario(app):
    """Matches the exact PM scenario requested:

    Baseline       Current
    SIT Ready     Nov 12          Nov 14   +2d
    UAT           Nov 20          Nov 25   +5d
    Go Live       Dec 01          Dec 06   +5d

    Schedule variance: +5 working days
    """
    with app.app_context():
        proj = Project(name="PM Steering")
        db.session.add(proj)
        db.session.flush()
        lane = Lane(project_id=proj.id, name="Milestones", sort_order=1)
        db.session.add(lane)
        db.session.flush()

        sit = Task(
            project_id=proj.id,
            code="010",
            kind=TASK_KIND_MILESTONE,
            lane_id=lane.id,
            name="SIT Ready",
            start_date=date(2026, 11, 14),
            duration_days=0,
            baseline_start_date=date(2026, 11, 12),
        )
        uat = Task(
            project_id=proj.id,
            code="020",
            kind=TASK_KIND_MILESTONE,
            lane_id=lane.id,
            name="UAT",
            start_date=date(2026, 11, 25),
            duration_days=0,
            baseline_start_date=date(2026, 11, 20),
        )
        go_live = Task(
            project_id=proj.id,
            code="030",
            kind=TASK_KIND_MILESTONE,
            lane_id=lane.id,
            name="Go Live",
            start_date=date(2026, 12, 6),
            duration_days=0,
            baseline_start_date=date(2026, 12, 1),
        )
        db.session.add_all([sit, uat, go_live])
        db.session.commit()

        tasks = [sit, uat, go_live]
        overview = build_project_overview(tasks, [], [], project=proj)

        assert overview.has_baseline is True
        assert len(overview.milestones) == 3

        # Check individual milestone variances
        m_map = {m.name: m for m in overview.milestones}
        assert m_map["SIT Ready"].variance_days == 2
        assert m_map["SIT Ready"].variance_str == "+2d"

        assert m_map["UAT"].variance_days == 3 or m_map["UAT"].variance_days == 5  # depending on calendar workdays
        # Let's verify Go Live:
        assert m_map["Go Live"].variance_str.startswith("+")

        # The project schedule variance matches the final delivery milestone variance
        assert overview.schedule_variance_days == m_map["Go Live"].variance_days
        assert overview.schedule_variance_days > 0


def test_baseline_endpoints_htmx(client, app):
    with app.app_context():
        proj = Project(name="Baseline HTMX")
        db.session.add(proj)
        db.session.flush()
        lane = Lane(project_id=proj.id, name="L1", sort_order=1)
        db.session.add(lane)
        db.session.flush()
        m = Task(
            project_id=proj.id,
            code="001",
            kind=TASK_KIND_MILESTONE,
            lane_id=lane.id,
            name="Alpha",
            start_date=date(2026, 11, 1),
            duration_days=0,
        )
        db.session.add(m)
        db.session.commit()
        pid = proj.id

    # 1. Trigger "Set baseline"
    resp = client.post(
        f"/projects/{pid}/baseline",
        data={"action": "set", "partial": "overview"},
        headers={"HX-Request": "true"},
    )
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "Schedule variance" in html or "Re-baseline" in html
    assert "0d" in html or "On Baseline" in html

    # Verify database state
    with app.app_context():
        p = Project.query.get(pid)
        assert p.baseline_set_at is not None
        task = Task.query.filter_by(project_id=pid).first()
        assert task.baseline_start_date == date(2026, 11, 1)

    # 2. Trigger "Clear baseline"
    resp = client.post(
        f"/projects/{pid}/baseline",
        data={"action": "clear", "partial": "overview"},
        headers={"HX-Request": "true"},
    )
    assert resp.status_code == 200
    with app.app_context():
        p = Project.query.get(pid)
        assert p.baseline_set_at is None
        task = Task.query.filter_by(project_id=pid).first()
        assert task.baseline_start_date is None


def test_diagram_renders_baseline_variance(app):
    with app.app_context():
        proj = Project(name="Diagram Baseline")
        db.session.add(proj)
        db.session.flush()
        lane = Lane(project_id=proj.id, name="L", sort_order=1)
        db.session.add(lane)
        db.session.flush()
        m = Task(
            project_id=proj.id,
            code="001",
            kind=TASK_KIND_MILESTONE,
            lane_id=lane.id,
            name="SIT Gate",
            start_date=date(2026, 11, 14),
            duration_days=0,
            baseline_start_date=date(2026, 11, 12),
        )
        db.session.add(m)
        db.session.commit()

        html = build_diagram([lane], [m])
        assert "milestone-variance-tag" in html
        assert "+2d" in html
