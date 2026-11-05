"""Tests for Project Working Days and Holidays Calendar."""

from datetime import date
import pytest

from app import create_app
from app.db import db
from app.models import Project, Lane, Task, Holiday, TASK_KIND_MILESTONE
from app.workdays import WorkdayCalendar, add_workdays, count_workdays, is_workday, next_workday
from app.services.scheduler import recalculate_project


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


def test_custom_workday_calendar():
    # 6-day work week: Mon-Sat (0..5)
    cal_6d = WorkdayCalendar(workdays={0, 1, 2, 3, 4, 5})
    fri = date(2026, 12, 18)
    sat = date(2026, 12, 19)
    sun = date(2026, 12, 20)
    mon = date(2026, 12, 21)

    assert cal_6d.is_workday(sat) is True
    assert cal_6d.is_workday(sun) is False
    assert cal_6d.next_workday(sun) == mon
    # Friday + 1 workday in 6-day week lands on Saturday:
    assert cal_6d.add_workdays(fri, 1) == sat
    # Friday + 2 workdays lands on Monday:
    assert cal_6d.add_workdays(fri, 2) == mon
    assert cal_6d.count_workdays(fri, mon) == 2


def test_workdays_with_holidays():
    """Test user requested scenario:
    Holidays:
    Dec 25 Christmas
    Dec 26 Boxing Day
    Jan 01 New Year
    """
    xmas = date(2026, 12, 25)  # Friday
    boxing = date(2026, 12, 26)  # Saturday
    new_year = date(2027, 1, 1)  # Friday
    holidays = {xmas, boxing, new_year}

    cal = WorkdayCalendar(workdays={0, 1, 2, 3, 4}, holidays=holidays)

    thu = date(2026, 12, 24)
    mon = date(2026, 12, 28)

    assert cal.is_workday(xmas) is False
    assert cal.is_workday(thu) is True

    # Next workday after Thursday Dec 24:
    # Fri Dec 25 is holiday, Sat 26 is weekend, Sun 27 is weekend -> Mon Dec 28
    assert cal.add_workdays(thu, 1) == mon
    assert cal.count_workdays(thu, mon) == 1

    # Starting directly on Xmas rolls forward to Mon Dec 28
    assert cal.next_workday(xmas) == mon


def test_project_task_end_date_uses_calendar(app):
    with app.app_context():
        proj = Project(name="Cal Proj")
        db.session.add(proj)
        db.session.flush()
        lane = Lane(project_id=proj.id, name="General", sort_order=1)
        db.session.add(lane)
        db.session.flush()

        # Add holiday on Friday Dec 25
        h = Holiday(project_id=proj.id, date=date(2026, 12, 25), name="Christmas")
        db.session.add(h)
        db.session.commit()

        # Task starting Thu Dec 24 with duration 2 days:
        # Day 1: Thu Dec 24.
        # Day 2: skipped Fri Dec 25 (Xmas), Sat 26, Sun 27 -> Mon Dec 28.
        # End date (exclusive finish): Tue Dec 29.
        t = Task(
            project_id=proj.id,
            code="001",
            lane_id=lane.id,
            name="Holiday Task",
            start_date=date(2026, 12, 24),
            duration_days=2,
        )
        db.session.add(t)
        db.session.commit()

        assert t.end_date == date(2026, 12, 29)


def test_recalculate_project_with_holidays(app):
    with app.app_context():
        proj = Project(name="Recalc Cal")
        db.session.add(proj)
        db.session.flush()
        lane = Lane(project_id=proj.id, name="Dev", sort_order=1)
        db.session.add(lane)
        db.session.flush()

        # Add holiday on Friday Dec 25
        db.session.add(Holiday(project_id=proj.id, date=date(2026, 12, 25), name="Christmas"))

        # t1: Thu Dec 24, dur 1 day -> finishes Mon Dec 28 (exclusive finish date)
        t1 = Task(
            project_id=proj.id,
            code="001",
            lane_id=lane.id,
            name="T1",
            start_date=date(2026, 12, 24),
            duration_days=1,
        )
        # t2: depends on 001. Initially set to Dec 24.
        t2 = Task(
            project_id=proj.id,
            code="002",
            lane_id=lane.id,
            name="T2",
            start_date=date(2026, 12, 24),
            duration_days=1,
            predecessors="001",
        )
        # t3: task accidentally created on Christmas day
        t3 = Task(
            project_id=proj.id,
            code="003",
            lane_id=lane.id,
            name="T3",
            start_date=date(2026, 12, 25),
            duration_days=1,
        )
        db.session.add_all([t1, t2, t3])
        db.session.commit()

        recalculate_project(proj.id)
        db.session.commit()

        # t2 must start on Monday Dec 28 because Friday Dec 25 was holiday + weekend skipped
        assert t2.start_date == date(2026, 12, 28)
        # t3 must be rolled forward from Christmas to Monday Dec 28
        assert t3.start_date == date(2026, 12, 28)


def test_update_workdays_endpoint(client, app):
    with app.app_context():
        proj = Project(name="Workdays Test")
        db.session.add(proj)
        db.session.commit()
        pid = proj.id

    # Select Mon-Sat (0, 1, 2, 3, 4, 5)
    resp = client.post(
        f"/projects/{pid}/workdays",
        data={"workdays": ["0", "1", "2", "3", "4", "5"], "partial": "settings"},
        headers={"HX-Request": "true"},
    )
    assert resp.status_code == 200
    assert "HX-Trigger-After-Swap" in resp.headers

    with app.app_context():
        p = Project.query.get(pid)
        assert p.workdays == "0,1,2,3,4,5"
        assert p.workdays_set == {0, 1, 2, 3, 4, 5}


def test_manage_holidays_endpoint(client, app):
    with app.app_context():
        proj = Project(name="Holidays Test")
        db.session.add(proj)
        db.session.commit()
        pid = proj.id

    # 1. Add Christmas holiday
    resp = client.post(
        f"/projects/{pid}/holidays",
        data={"action": "add", "date": "2026-12-25", "name": "Christmas", "partial": "settings"},
        headers={"HX-Request": "true"},
    )
    assert resp.status_code == 200
    assert "Christmas" in resp.get_data(as_text=True)

    with app.app_context():
        h = Holiday.query.filter_by(project_id=pid).first()
        assert h is not None
        assert h.name == "Christmas"
        assert h.date == date(2026, 12, 25)
        hid = h.id

    # 2. Delete holiday
    resp = client.post(
        f"/projects/{pid}/holidays",
        data={"action": "delete", "holiday_id": str(hid), "partial": "settings"},
        headers={"HX-Request": "true"},
    )
    assert resp.status_code == 200
    with app.app_context():
        assert Holiday.query.filter_by(project_id=pid).count() == 0
