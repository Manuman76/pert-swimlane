"""The 5-day week: Saturday and Sunday are never task time.

Unit tests for :mod:`app.workdays` (the only place that knows how to skip a
weekend) plus the two entry points that feed dates in: a typed start date and
the default start date of a newly added task.
"""
from datetime import date

import pytest

from app import create_app
from app.db import db
from app.models import Lane, Project, Task
from app.routes import parse_date
from app.workdays import add_workdays, count_workdays, is_workday, next_workday

# June 2017: Thu 1, Fri 2, Sat 3, Sun 4, Mon 5 ... Fri 30.
MON, TUE, WED, THU, FRI, SAT, SUN = (date(2017, 6, d) for d in (5, 6, 7, 1, 2, 3, 4))


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


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def test_is_workday_and_next_workday():
    assert [is_workday(d) for d in (THU, FRI, SAT, SUN, MON)] == [True, True, False, False, True]
    assert next_workday(SAT) == MON
    assert next_workday(SUN) == MON
    assert next_workday(THU) == THU       # already a working day: unchanged


@pytest.mark.parametrize("start,days,expected", [
    (THU, 0, THU),
    (THU, 1, FRI),
    (THU, 3, TUE),      # Thu + Fri + Mon -> finish Tue, the weekend is skipped
    (FRI, 1, MON),
    (SAT, 1, TUE),      # a weekend start counts from Monday
    (MON, 5, date(2017, 6, 12)),   # a full working week -> next Monday, not Saturday
    (MON, 10, date(2017, 6, 19)),
    (THU, 20, date(2017, 6, 29)),  # whole weeks are jumped, not looped
])
def test_add_workdays(start, days, expected):
    result = add_workdays(start, days)
    assert result == expected
    assert is_workday(result)


def test_add_workdays_rejects_negative():
    with pytest.raises(ValueError):
        add_workdays(MON, -1)


@pytest.mark.parametrize("start,days", [
    (THU, 0), (THU, 1), (THU, 3), (FRI, 1), (MON, 5), (WED, 13), (SAT, 4),
])
def test_count_workdays_inverts_add_workdays(start, days):
    assert count_workdays(start, add_workdays(start, days)) == days


def test_count_workdays_empty_or_inverted_range():
    assert count_workdays(MON, MON) == 0
    assert count_workdays(TUE, MON) == 0
    assert count_workdays(None, MON) == 0


def test_count_workdays_ignores_weekends():
    # Mon 5 .. Fri 9 inclusive is 5 working days; Sat 10 / Sun 11 are not.
    assert count_workdays(date(2017, 6, 5), date(2017, 6, 12)) == 5
    # A pure weekend range holds no task time at all ([start, end) is Sat+Sun).
    assert count_workdays(date(2017, 6, 3), date(2017, 6, 5)) == 0
    assert count_workdays(date(2017, 6, 3), date(2017, 6, 6)) == 1   # only Mon


# ---------------------------------------------------------------------------
# entry points
# ---------------------------------------------------------------------------

def test_parse_date_rolls_weekend_forward():
    assert parse_date("3 Jun 2017") == MON       # Saturday typed -> Monday
    assert parse_date("2017-06-04") == MON       # ISO input too
    assert parse_date("1 Jun 2017") == THU       # working day untouched


def test_update_route_moves_weekend_start_to_monday(client, app):
    with app.app_context():
        proj = Project(name="W1")
        db.session.add(proj)
        db.session.commit()
        lane = Lane(project_id=proj.id, name="L1", sort_order=1)
        db.session.add(lane)
        db.session.commit()
        task = Task(project_id=proj.id, code="001", lane_id=lane.id, name="T1",
                    start_date=THU, duration_days=2)
        db.session.add(task)
        db.session.commit()

        resp = client.post(f"/tasks/{task.id}/update", data={
            "name": "T1",
            "lane_id": str(lane.id),
            "start_date": "4 Jun 2017",   # a Sunday
            "duration_days": "2",
        })
        assert resp.status_code == 200

        db.session.expire_all()
        saved = Task.query.get(task.id)
        assert saved.start_date == MON                    # rolled onto Monday
        assert saved.end_date == WED                      # Mon + Tue -> Wed
        assert "5 Jun 2017" in resp.get_data(as_text=True)


def test_create_task_starts_on_a_workday(client, app, monkeypatch):
    """Adding a task over a weekend anchors it on Monday, not Saturday."""
    import app.routes as routes

    class _FakeDate:
        @staticmethod
        def today():
            return SAT          # 2017-06-03, a Saturday

    monkeypatch.setattr(routes, "date", _FakeDate)

    with app.app_context():
        proj = Project(name="W2")
        db.session.add(proj)
        db.session.commit()
        lane = Lane(project_id=proj.id, name="L1", sort_order=1)
        db.session.add(lane)
        db.session.commit()
        pid = proj.id

        resp = client.post(f"/projects/{pid}/tasks", data={"lane_id": str(lane.id)})
        assert resp.status_code == 200

        task = Task.query.filter_by(project_id=pid).one()
        assert task.start_date == MON
