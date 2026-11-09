"""P2: capacity settings routes — member availability fields and absences CRUD."""

from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from app import create_app
from app.capacity import build_capacity
from app.db import db
from app.models import (
    Project,
    Lane,
    Task,
    TeamMember,
    MemberAbsence,
    CAPACITY_PERCENT_MAX,
)
import app.routes.projects as projects_routes
from app.routes.settings import ROLE_MAX_LENGTH, update_member_capacity
from app.routes.capacity import absence_preview, parse_absence_range
from app.services.common import absences_in_order, member_roles_in_use


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


MON = date(2026, 6, 1)          # Monday


def make_project(name="Board", workdays="0,1,2,3,4"):
    proj = Project(name=name, workdays=workdays)
    db.session.add(proj)
    db.session.flush()
    lane = Lane(project_id=proj.id, name="Lane", sort_order=1)
    db.session.add(lane)
    db.session.commit()
    return proj


def member(project, name, **kwargs):
    row = TeamMember(project_id=project.id, name=name, **kwargs)
    db.session.add(row)
    db.session.commit()
    return row


def project_tasks(project):
    return Task.query.filter_by(project_id=project.id).all()


def capacity_for(project, **kwargs):
    """Run the engine the way a page render would: this project's people and tasks."""
    return build_capacity(project, project_tasks(project),
                          TeamMember.query.filter_by(project_id=project.id).all(), **kwargs)


def save_availability(client, project, member_row, **fields):
    """Post the member availability form the way the settings card does."""
    data = {"action": "capacity", "member_id": member_row.id, "partial": "settings"}
    for key, value in fields.items():
        data[key] = "" if value is None else str(value)
    return client.post(f"/projects/{project.id}/members", data=data)


def record_absence(client, project, member_row, start, end=None, reason=None, member_id=None):
    """Post the availability form below the member list."""
    data = {
        "action": "add", "partial": "settings",
        "member_id": member_row.id if member_row is not None else member_id,
        "start_date": start, "end_date": end or "", "reason": reason or "",
    }
    return client.post(f"/projects/{project.id}/absences", data=data)


def task(project, lane, code, responsible=None, start=MON, duration=5, **kwargs):
    row = Task(project_id=project.id, lane_id=lane.id, code=code, name=f"Task {code}",
               responsible=responsible, start_date=start, duration_days=duration, **kwargs)
    db.session.add(row)
    db.session.commit()
    return row


# ---------------------------------------------------------------------------
# Saving availability on a member
# ---------------------------------------------------------------------------

def test_capacity_action_saves_role_percent_and_cost(client):
    proj = make_project()
    alice = member(proj, "Alice")

    r = save_availability(client, proj, alice,
                          role="Backend developer", capacity_percent="60", cost_per_day="480.5")
    assert r.status_code == 200
    db.session.refresh(alice)
    assert (alice.role, alice.capacity_percent) == ("Backend developer", 60)
    assert float(alice.cost_per_day) == 480.5
    assert alice.capacity_fraction == pytest.approx(0.6)


def test_blank_fields_mean_full_time_and_untracked_cost(client):
    proj = make_project()
    bob = member(proj, "Bob", role="QA", capacity_percent=50, cost_per_day=200)

    save_availability(client, proj, bob, role=None, capacity_percent=None, cost_per_day=None)
    db.session.refresh(bob)
    assert (bob.role, bob.capacity_percent, bob.cost_per_day) == (None, 100, None)


@pytest.mark.parametrize("raw", ["", "  ", None])
def test_missing_capacity_falls_back_to_100(app, raw):
    proj = make_project()
    carol = member(proj, "Carol", capacity_percent=30)
    with app.test_request_context():
        update_member_capacity(proj, carol.id, capacity_percent=raw)
    db.session.refresh(carol)
    assert carol.capacity_percent == 100


@pytest.mark.parametrize("raw,expected", [("0", 0), ("100", 100), (" 60 ", 60),
                                          ("200", 200), (str(CAPACITY_PERCENT_MAX), CAPACITY_PERCENT_MAX)])
def test_capacity_accepts_padded_whole_percentages(client, raw, expected):
    proj = make_project()
    dave = member(proj, "Dave", capacity_percent=100)
    r = save_availability(client, proj, dave, capacity_percent=raw)
    assert r.status_code == 200
    db.session.refresh(dave)
    assert dave.capacity_percent == expected


@pytest.mark.parametrize("raw", ["abc", "6O", "60x", "60%", "1.5", "-5", str(CAPACITY_PERCENT_MAX + 1), "0.6"])
def test_nonsense_capacity_is_rejected_without_touching_the_member(client, raw):
    proj = make_project()
    erin = member(proj, "Erin", role="Design", capacity_percent=75, cost_per_day=300)
    r = save_availability(client, proj, erin, capacity_percent=raw)
    assert r.status_code == 400
    db.session.refresh(erin)
    assert (erin.role, erin.capacity_percent, float(erin.cost_per_day)) == ("Design", 75, 300.0)


@pytest.mark.parametrize("raw,expected", [("480", 480.0), ("480.50", 480.5), (" 480,75 ", 480.75),
                                          ("0", 0.0), ("0.004", 0.0)])
def test_cost_per_day_accepts_both_decimal_separators(client, raw, expected):
    proj = make_project()
    member_row = member(proj, "Fay")
    r = save_availability(client, proj, member_row, cost_per_day=raw)
    assert r.status_code == 200
    db.session.refresh(member_row)
    assert float(member_row.cost_per_day) == pytest.approx(expected)


@pytest.mark.parametrize("raw", ["abc", "-1", "999999.999", "1.2.3", "480abc"])
def test_nonsense_cost_is_rejected(client, raw):
    proj = make_project()
    member_row = member(proj, "Gus", cost_per_day=111)
    r = save_availability(client, proj, member_row, cost_per_day=raw)
    assert r.status_code == 400
    db.session.refresh(member_row)
    assert float(member_row.cost_per_day) == 111.0


def test_long_role_is_truncated_like_the_responsible_field(client):
    proj = make_project()
    member_row = member(proj, "Hal")
    save_availability(client, proj, member_row, role="x" * (ROLE_MAX_LENGTH + 30))
    db.session.refresh(member_row)
    assert len(member_row.role) == ROLE_MAX_LENGTH


def test_capacity_save_never_replans_the_schedule(client):
    proj = make_project()
    lane = proj.lanes[0]
    t = task(proj, lane, "T-001", responsible="Ivy", start=MON, duration=5)
    before = (t.start_date, t.end_date)
    member_row = member(proj, "Ivy", capacity_percent=100)

    save_availability(client, proj, member_row, capacity_percent="20")
    db.session.refresh(t)
    assert (t.start_date, t.end_date) == before


def test_rename_keeps_the_availability_fields(client):
    proj = make_project()
    member_row = member(proj, "Jack", role="Lead", capacity_percent=80, cost_per_day=500)
    task(proj, proj.lanes[0], "T-001", responsible="Jack")

    client.post(f"/projects/{proj.id}/members", data={
        "action": "rename", "member_id": member_row.id, "name": "Jacqueline",
    })
    db.session.refresh(member_row)
    assert member_row.name == "Jacqueline"
    assert (member_row.role, member_row.capacity_percent) == ("Lead", 80)
    assert Task.query.filter_by(project_id=proj.id, responsible="Jacqueline").count() == 1


def test_add_member_can_carry_an_initial_capacity(client):
    proj = make_project()
    client.post(f"/projects/{proj.id}/members", data={
        "action": "add", "name": "Kate", "capacity_percent": "40", "partial": "settings",
    })
    kate = TeamMember.query.filter_by(project_id=proj.id, name="Kate").one()
    assert kate.capacity_percent == 40


def test_add_member_with_bad_capacity_creates_nobody(client):
    proj = make_project()
    r = client.post(f"/projects/{proj.id}/members", data={
        "action": "add", "name": "Leo", "capacity_percent": "everything",
    })
    assert r.status_code == 400
    assert TeamMember.query.filter_by(project_id=proj.id, name="Leo").first() is None


def test_availability_edits_do_not_rename_or_free_tasks(client):
    """A person at 40% is still the person: their tasks stay assigned to them."""
    proj = make_project()
    task(proj, proj.lanes[0], "T-001", responsible="Mia")
    mia = member(proj, "Mia")
    save_availability(client, proj, mia, capacity_percent="40")
    assert Task.query.filter_by(project_id=proj.id, responsible="Mia").count() == 1


def test_member_id_from_another_project_is_not_found(client):
    mine, theirs = make_project("Mine"), make_project("Theirs")
    stranger = member(theirs, "Ned")
    r = save_availability(client, mine, stranger, capacity_percent="50")
    assert r.status_code == 404
    r = client.post(f"/projects/{mine.id}/members", data={
        "action": "capacity", "member_id": 999999, "capacity_percent": "50", "partial": "settings",
    })
    assert r.status_code == 404


def test_capacity_form_rerenders_the_member_list(client):
    proj = make_project()
    member(proj, "Olga", role="PO")
    vera = member(proj, "Vera")
    r = save_availability(client, proj, vera, capacity_percent="65", role="Dev")
    body = r.get_data(as_text=True)
    assert "Olga" in body and "Vera" in body
    assert 'value="65"' in body
    assert 'name="capacity_percent"' in body
    assert 'value="Dev"' in body
    assert 'id="member-roles"' in body       # datalist for typing a role again


def test_role_suggestions_are_deduped_sorted_and_blank_free(app):
    proj = make_project()
    member(proj, "Zoe", role="Tester")
    member(proj, "Ann", role="Backend")
    member(proj, "Bob", role="backend")
    member(proj, "Cal", role="Tester")      # the same role typed twice
    member(proj, "Dee", role="  Tester  ")  # ...typed with spaces
    member(proj, "Dot")                     # no role yet: nothing to suggest
    member(proj, "Eli", role="   ")
    # Case is kept (they are different words to a planner), spacing is not, and the
    # list is in a stable order so the datalist does not reshuffle between renders.
    assert member_roles_in_use(proj.id) == ["Backend", "backend", "Tester"]

    other = make_project("Other")
    member(other, "Fern", role="Finance")
    assert member_roles_in_use(proj.id) == ["Backend", "backend", "Tester"]


def test_the_role_datalist_reaches_the_settings_fragment(client):
    proj = make_project()
    member(proj, "Wes", role="Designer")
    spare = member(proj, "Yara")
    r = client.post(f"/projects/{proj.id}/members", data={
        "action": "delete", "member_id": spare.id, "partial": "settings",
    })
    assert '<option value="Designer">' in r.get_data(as_text=True)


# ---------------------------------------------------------------------------
# Absences
# ---------------------------------------------------------------------------

def test_add_absence_stores_an_exclusive_end(client):
    proj = make_project()
    zoe = member(proj, "Zoe")
    r = record_absence(client, proj, zoe, "2026-07-06", "2026-07-10", reason="Annual leave")
    assert r.status_code == 200
    absence = MemberAbsence.query.filter_by(project_id=proj.id).one()
    assert absence.start_date == date(2026, 7, 6)
    assert absence.end_date == date(2026, 7, 11), "the last day typed is still away"
    assert absence.reason == "Annual leave"
    assert absence.label == "6 - 10 Jul 2026"


def test_single_day_absence_needs_only_a_start(client):
    proj = make_project()
    person = member(proj, "Ann")
    record_absence(client, proj, person, "2026-07-06", "  ")
    absence = MemberAbsence.query.one()
    assert absence.end_date == date(2026, 7, 7)
    assert absence.reason is None
    assert absence.label == "6 Jul 2026"
    assert absence.is_multi_day is False


@pytest.mark.parametrize("raw", ["2026-07-06", "6 Jul 2026", "6 July 2026", "Jul 6 2026"])
def test_start_date_accepts_the_formats_settings_already_accepts(client, raw):
    proj = make_project()
    person = member(proj, "Ben")
    record_absence(client, proj, person, raw)
    assert MemberAbsence.query.filter_by(project_id=proj.id).one().start_date == date(2026, 7, 6)


def test_short_date_borrows_the_current_year(client):
    proj = make_project()
    person = member(proj, "Cal")
    record_absence(client, proj, person, "Jul 6")
    absence = MemberAbsence.query.one()
    assert (absence.start_date.month, absence.start_date.day) == (7, 6)
    assert absence.start_date.year == date.today().year


def test_parse_absence_range_is_reusable():
    start, end, error = parse_absence_range("2026-07-06", "2026-07-06")
    assert (start, end, error) == (date(2026, 7, 6), date(2026, 7, 7), None)


@pytest.mark.parametrize("start,end,snippet", [
    ("", "2026-07-10", "start date is required"),
    ("nonsense", "", "start date is required"),
    ("2026-07-06", "nonsense", "end date"),
    ("2026-07-10", "2026-07-06", "cannot be before"),
    ("2026-07-06", "2027-09-01", "at most"),
])
def test_bad_absence_ranges_are_refused(client, start, end, snippet):
    proj = make_project()
    person = member(proj, "Dot")
    r = record_absence(client, proj, person, start, end)
    assert r.status_code == 400
    assert snippet in r.get_data(as_text=True)
    assert MemberAbsence.query.filter_by(project_id=proj.id).count() == 0


def test_absence_needs_a_member_of_this_project(client):
    mine, theirs = make_project("Mine"), make_project("Theirs")
    stranger = member(theirs, "Eli")
    mine_id, theirs_id = mine.id, theirs.id

    r = client.post(f"/projects/{mine_id}/absences", data={
        "action": "add", "start_date": "2026-07-06",
    })
    assert r.status_code == 400 and "member" in r.get_data(as_text=True).lower()

    r = client.post(f"/projects/{mine_id}/absences", data={
        "action": "add", "member_id": stranger.id, "start_date": "2026-07-06",
    })
    assert r.status_code == 400
    assert MemberAbsence.query.filter_by(project_id=theirs_id).count() == 0


def test_the_same_absence_twice_is_refused_but_a_different_range_is_not(client):
    proj = make_project()
    person = member(proj, "Fiti")
    assert record_absence(client, proj, person, "2026-07-06", "2026-07-10").status_code == 200

    r = record_absence(client, proj, person, "2026-07-06", "2026-07-10")
    assert r.status_code == 400
    assert "already recorded" in r.get_data(as_text=True)

    # A wider overlapping block is a different fact about availability, and the
    # week arithmetic counts each away day once regardless.
    r = record_absence(client, proj, person, "2026-07-08", "2026-07-20")
    assert r.status_code == 200
    assert MemberAbsence.query.filter_by(project_id=proj.id).count() == 2


def test_delete_absence_and_its_bounds(client):
    proj = make_project()
    person = member(proj, "Gil")
    record_absence(client, proj, person, "2026-07-06")
    absence = MemberAbsence.query.one()

    r = client.post(f"/projects/{proj.id}/absences", data={
        "action": "delete", "absence_id": absence.id, "partial": "settings",
    })
    assert r.status_code == 200
    assert MemberAbsence.query.filter_by(project_id=proj.id).count() == 0
    assert "No absences recorded" in r.get_data(as_text=True)

    r = client.post(f"/projects/{proj.id}/absences", data={"action": "delete", "absence_id": absence.id})
    assert r.status_code == 404

    other = make_project("Other")
    stranger = member(other, "Gil")
    record_absence(client, other, stranger, "2026-07-06")
    foreign = MemberAbsence.query.filter_by(project_id=other.id).one()
    assert client.post(f"/projects/{proj.id}/absences", data={
        "action": "delete", "absence_id": foreign.id,
    }).status_code == 404


def test_unknown_absence_action_is_a_400(client):
    proj = make_project()
    assert client.post(f"/projects/{proj.id}/absences", data={"action": "copy"}).status_code == 400


def test_absence_listing_is_scoped_to_the_project(client):
    mine, theirs = make_project("Mine"), make_project("Theirs")
    ann = member(mine, "Ann")
    bob = member(theirs, "Bob")
    record_absence(client, mine, ann, "2026-07-06", reason="Sick")
    record_absence(client, theirs, bob, "2026-08-03")

    mine_ids = [a.id for a in absences_in_order(mine.id)]
    assert len(mine_ids) == 1
    body = client.get(f"/projects/{mine.id}").get_data(as_text=True)
    assert "Sick" in body and "Bob" not in body


def test_absences_list_by_person_then_by_date(client):
    proj = make_project()
    ann = member(proj, "Ann")
    zoe = member(proj, "Zoe")
    for person, start in ((zoe, "2026-08-03"), (ann, "2026-09-01"), (ann, "2026-07-06")):
        record_absence(client, proj, person, start)
    assert [(a.member.name, a.start_date) for a in absences_in_order(proj.id)] == [
        ("Ann", date(2026, 7, 6)), ("Ann", date(2026, 9, 1)), ("Zoe", date(2026, 8, 3)),
    ]


def test_htmx_post_returns_the_availability_fragment(client):
    proj = make_project()
    person = member(proj, "Ann")
    r = client.post(f"/projects/{proj.id}/absences", headers={"HX-Request": "true"},
                    data={"action": "add", "member_id": person.id, "start_date": "2026-07-06"})
    body = r.get_data(as_text=True)
    assert r.status_code == 200
    assert 'id="settings-absences-container"' in body
    assert "6 Jul 2026" in body and "Ann" in body
    assert 'name="action" value="delete"' in body


def test_the_absence_picker_shows_who_is_already_part_time(client):
    proj = make_project()
    save_availability(client, proj, member(proj, "Ann"), capacity_percent="40")
    member(proj, "Bob")
    body = client.get(f"/projects/{proj.id}").get_data(as_text=True)
    assert "Ann &middot; 40%" in body      # choosing whom to book should not be a guess
    assert ">Bob<" in body                 # ...and full-time people are not annotated


def test_plain_post_redirects_back_to_settings(client):
    proj = make_project()
    person = member(proj, "Ann")
    r = client.post(f"/projects/{proj.id}/absences",
                    data={"member_id": person.id, "start_date": "2026-07-06"})
    assert r.status_code == 302
    assert r.headers["Location"].endswith("/projects/%d#settings" % proj.id)


def test_absence_member_names_are_escaped(client):
    proj = make_project()
    tricky = member(proj, '<img src=x onerror=alert(1)>')
    record_absence(client, proj, tricky, "2026-07-06", reason="<b>x</b>")
    body = client.get(f"/projects/{proj.id}").get_data(as_text=True)
    assert "<img src=x onerror" not in body
    assert "&lt;img src=x" in body


def test_removing_a_member_removes_their_availability(client):
    proj = make_project()
    person = member(proj, "Ann")
    record_absence(client, proj, person, "2026-07-06")
    client.post(f"/projects/{proj.id}/members", data={
        "action": "delete", "member_id": person.id, "partial": "settings"})
    assert MemberAbsence.query.filter_by(project_id=proj.id).count() == 0
    assert "Everyone is available" in client.get(f"/projects/{proj.id}").get_data(as_text=True)


def test_deleting_a_project_takes_its_availability_with_it(client):
    """An orphan absence would be listed against nobody on the next project page."""
    proj = make_project()
    person = member(proj, "Ann")
    record_absence(client, proj, person, "2026-07-06")
    assert client.post(f"/projects/{proj.id}/delete").status_code == 302
    assert MemberAbsence.query.count() == 0
    assert TeamMember.query.count() == 0


def test_absence_does_not_move_any_task_date(client):
    proj = make_project()
    lane = proj.lanes[0]
    member(proj, "Ann")
    t = task(proj, lane, "T-001", responsible="Ann", start=MON, duration=5)
    before = (t.start_date, t.end_date)
    record_absence(client, proj, TeamMember.query.filter_by(name="Ann").one(),
                   MON, MON + timedelta(days=4))
    db.session.refresh(t)
    assert (t.start_date, t.end_date) == before


# ---------------------------------------------------------------------------
# What the availability list says about the load
# ---------------------------------------------------------------------------

def preview_of(project, **kwargs):
    """The settings-page preview, pinned to a Monday so it never drifts."""
    kwargs.setdefault("today", MON)
    return absence_preview(project, **kwargs)


def test_the_preview_covers_eight_whole_weeks_from_today(app):
    proj = make_project()
    member(proj, "Ann")
    view = preview_of(proj)
    assert view.label == "01 Jun 2026 - 26 Jul 2026"
    assert (view.weeks, view.from_date, view.to_date) == (8, MON, date(2026, 7, 26))
    assert view.warnings == {} and view.outside == []


def test_the_preview_says_nothing_when_there_is_nobody_to_plan_for(app):
    assert preview_of(make_project("Empty")) is None


def test_the_preview_names_the_weeks_behind_a_far_away_absence(client):
    proj = make_project()
    ann = member(proj, "Ann")
    record_absence(client, proj, ann, date(2026, 11, 2), date(2026, 11, 8))   # Mon-Sun

    view = preview_of(proj)
    assert view.weeks == 23                                  # ...so the window grows
    assert view.to_date == date(2026, 11, 8)                 # to Sunday, not the Monday after
    assert view.outside == []


def test_an_absence_past_the_planning_horizon_is_counted_not_dropped(client):
    proj = make_project()
    ann = member(proj, "Ann")
    record_absence(client, proj, ann, date(2027, 6, 1))      # a year out

    view = preview_of(proj)
    assert view.weeks == 26                                  # CAPACITY_MAX_WEEKS
    assert [a.start_date for a in view.outside] == [date(2027, 6, 1)]


def test_an_absence_in_an_overloaded_week_is_flagged(client):
    proj = make_project()
    lane = proj.lanes[0]
    ann = member(proj, "Ann", capacity_percent=50)           # 2.5 days a week
    task(proj, lane, "T-001", responsible="Ann", start=MON, duration=5)   # 5 days
    record_absence(client, proj, ann, MON + timedelta(days=2))            # that week
    record_absence(client, proj, ann, MON + timedelta(weeks=1))           # quiet week

    view = preview_of(proj)
    peak, quiet = absences_in_order(proj.id)
    assert list(view.warnings) == [peak.id]                  # only the week that is over
    assert (view.warnings[peak.id].percent, view.warnings[peak.id].start) == (250, MON)
    assert view.warnings[peak.id].task_codes == ["T-001"]    # ...named by task code
    assert quiet.id not in view.warnings


def test_a_day_off_out_of_a_fully_booked_week_is_flagged(client):
    proj = make_project()
    lane = proj.lanes[0]
    ann = member(proj, "Ann")                                # 5 days a week
    task(proj, lane, "T-001", responsible="Ann", start=MON, duration=5)
    record_absence(client, proj, ann, MON + timedelta(days=2))

    view = preview_of(proj)                                  # 5 of 4 days: no room
    assert [c.percent for c in view.warnings.values()] == [125]


def test_a_fully_booked_week_without_room_to_spare_is_not_a_warning(client):
    proj = make_project()
    lane = proj.lanes[0]
    ann = member(proj, "Ann")                                # 5 days a week
    task(proj, lane, "T-001", responsible="Ann", start=MON, duration=4)
    record_absence(client, proj, ann, MON)                   # 4 left, 4 booked

    view = preview_of(proj)
    assert [c.percent for c in view.warnings.values()] == []  # 100% is tight, not over


def test_a_quiet_absence_is_left_alone(client):
    proj = make_project()
    ann = member(proj, "Ann")
    record_absence(client, proj, ann, MON + timedelta(days=2))

    assert preview_of(proj).warnings == {}                   # nothing booked, nothing to warn


def test_an_absence_before_the_window_is_not_advised_about(client):
    proj = make_project()
    lane = proj.lanes[0]
    ann = member(proj, "Ann", capacity_percent=50)
    task(proj, lane, "T-001", responsible="Ann", start=MON, duration=5)
    record_absence(client, proj, ann, MON - timedelta(weeks=3))   # already happened

    view = preview_of(proj)
    assert view.warnings == {}
    assert view.outside == []                                 # ...it is simply in the past


def stub_cell(**over):
    """A week cell shaped like the engine's, so the template is driven by real fields."""
    cell = SimpleNamespace(demand_str="5d", supply_str="2.5d", percent_str="200%",
                           state="over", start=MON, task_codes=["T-001"])
    return SimpleNamespace(**{**vars(cell), **over})


def test_the_availability_list_prints_what_the_preview_found(client, monkeypatch):
    proj = make_project()
    ann = member(proj, "Ann")
    record_absence(client, proj, ann, MON, reason="Conference")
    away = absences_in_order(proj.id)[0]

    view = SimpleNamespace(weeks=8, from_date=MON, to_date=date(2026, 7, 26),
                           label="01 Jun 2026 - 26 Jul 2026",
                           warnings={away.id: stub_cell()}, outside=[away])
    monkeypatch.setattr(projects_routes, "absence_preview", lambda proj, **kwargs: view)

    body = client.get(f"/projects/{proj.id}").get_data(as_text=True)
    assert "Load checked from 01 Jun 2026 - 26 Jul 2026 (8 weeks)" in body
    assert "1 absence falls later" in body                     # singular, and honest
    assert "200% booked that week" in body
    # Quoted in full: "5d ..." is a substring of "2.5d ...", so the quote is the test.
    assert ('title="5d of work against 2.5d available in the week of 1 Jun 2026 (T-001)"'
            in body)


def test_the_availability_list_stays_plain_when_nothing_is_over(client, monkeypatch):
    proj = make_project()
    ann = member(proj, "Ann")
    record_absence(client, proj, ann, MON)
    away = absences_in_order(proj.id)[0]

    view = SimpleNamespace(weeks=8, from_date=MON, to_date=date(2026, 7, 26),
                           label="01 Jun 2026 - 26 Jul 2026", warnings={}, outside=[])
    monkeypatch.setattr(projects_routes, "absence_preview", lambda proj, **kwargs: view)

    body = client.get(f"/projects/{proj.id}").get_data(as_text=True)
    assert "booked that week" not in body
    assert "falls later" not in body
    assert away.reason is None and away.label in body          # the row itself is unchanged


# ---------------------------------------------------------------------------
# What the routes write, the engine reads
# ---------------------------------------------------------------------------

def test_settings_edits_are_visible_to_the_capacity_engine(client):
    proj = make_project()
    lane = proj.lanes[0]
    person = member(proj, "Ann")
    task(proj, lane, "T-001", responsible="Ann", start=MON, duration=5)

    week1 = capacity_for(proj, weeks=1, today=MON).rows[0].weeks[0]
    assert (week1.demand, week1.supply, week1.state) == (5.0, 5.0, "tight")

    save_availability(client, proj, person, capacity_percent="60")
    record_absence(client, proj, person, MON, MON + timedelta(days=2))

    week1 = capacity_for(proj, weeks=1, today=MON).rows[0].weeks[0]
    # Five working days, three of them away, at 60% availability - against a task
    # that did not move when either number changed.
    assert week1.demand == pytest.approx(5.0)
    assert week1.supply == pytest.approx((5 - 3) * 0.6)
    assert week1.state == "over"
    assert week1.percent > 400
