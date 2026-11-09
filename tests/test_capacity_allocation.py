"""P4: a task says how much of a person it wants.

Two halves. The detail modal gains one number - the share of its assignee's week the
task consumes - and the task grid gains one mark: when that person is already past a
full week in the very weeks the task needs. Neither stores anything about capacity;
the grid asks the same engine the Capacity tab asks.
"""

import re
from datetime import date, timedelta

import pytest

from app import create_app
from app.db import db
from app.models import (
    Lane,
    Project,
    Task,
    TeamMember,
    TASK_KIND_MILESTONE,
)
from app.routes.context import capacity_overload_map
from app.services.common import fmt_allocation, fmt_date, parse_percent_field

MON = date(2026, 6, 1)              # a Monday; W23 of 2026
WEEK = timedelta(weeks=1)


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


def client_of(app):
    return app.test_client()


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


def task(project, lane, code, responsible=None, start=MON, duration=5, **kwargs):
    row = Task(project_id=project.id, lane_id=lane.id, code=code, name=f"Task {code}",
               responsible=responsible, start_date=start, duration_days=duration, **kwargs)
    db.session.add(row)
    db.session.commit()
    return row


def reload(row):
    db.session.refresh(row)
    return row


def modal(client, row):
    return client.get(f"/tasks/{row.id}/detail")


def modal_form(row, **overrides):
    """Everything the modal posts, with the two numbers left blank by default."""
    data = {
        "name": row.name,
        "description": "",
        "notes": "",
        "links": "",
        "priority": row.priority or "medium",
        "status": row.status,
        "lane_name": row.lane.name,
        "start_date": fmt_date(row.start_date),
        "duration_days": str(row.duration_days),
        "responsible": row.responsible or "",
        "requirement_id": "",
        "progress_percent": "",
        "allocation_percent": "",
    }
    data.update({key: ("" if value is None else str(value)) for key, value in overrides.items()})
    return data


def save(client, row, **overrides):
    return client.post(f"/tasks/{row.id}/detail", data=modal_form(row, **overrides))


def page(client, project):
    return client.get(f"/projects/{project.id}").get_data(as_text=True)


def row_block(html, row):
    start = html.index(f'<tr id="task-row-{row.id}"')
    end = html.find("</tr>", start)
    return html[start:end]


def booked(client, project, **params):
    query = "&".join(f"{key}={value}" for key, value in params.items())
    url = f"/projects/{project.id}/capacity"
    return client.get(f"{url}?{query}" if query else url).get_data(as_text=True)


def load_of(client, project, week=MON):
    """What the Capacity tab says about one member's first week."""
    html = booked(client, project, start=week.strftime("%Y-%m-%d"), weeks=1)
    return re.search(r'<span class="capacity-cell-value">([^<]*)</span>', html).group(1)


# ---------------------------------------------------------------------------
# The parse rule, on its own
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw, expected", [
    ("", None),
    (None, None),
    ("   ", None),
    ("40", 40),
    (" 40 ", 40),
    ("0", 0),
    ("100", 100),
    ("١٢", 12),                     # decimal digits from another alphabet count
])
def test_a_share_of_a_person_reads_as_a_whole_number(raw, expected):
    value, error = parse_percent_field(raw, "Allocation")
    assert (value, error) == (expected, None)


@pytest.mark.parametrize("raw", ["101", "150", "-5", "60%", "1.5", "twelve", " 5x ", "\u00b2"])
def test_a_share_that_is_not_a_number_is_refused_not_stored(raw):
    """Including the superscript that used to reach ``int`` and raise a 500."""
    value, error = parse_percent_field(raw, "Allocation")
    assert value is None
    assert error == "Allocation must be a whole number from 0 to 100."


@pytest.mark.parametrize("raw, expected", [
    ("", "Whole of this person's time"),
    ("100", "Whole of this person's time"),
    ("40", "40% of this person's time"),
    ("0", "Nothing: this task asks no time of its owner"),
])
def test_the_share_says_itself_in_a_tooltip(raw, expected):
    assert fmt_allocation(type("T", (), {"allocation_percent":
                                         (None if raw == "" else int(raw))})()) == expected


# ---------------------------------------------------------------------------
# The modal field
# ---------------------------------------------------------------------------

def test_the_modal_asks_for_the_share_next_to_the_person(app):
    proj = make_project()
    member(proj, "Ann")
    row = task(proj, proj.lanes[0], "001", responsible="Ann")
    html = modal(client_of(app), row).get_data(as_text=True)
    assert 'name="allocation_percent"' in html
    assert 'id="detail-allocation"' in html
    assert 'placeholder="100"' in html
    assert "Share of this person's day while the task runs" in html


def test_an_untouched_field_still_means_the_whole_person(app):
    proj = make_project()
    member(proj, "Ann")
    lane = proj.lanes[0]
    row = task(proj, lane, "001", responsible="Ann", duration=5)
    save(client_of(app), row)
    assert reload(row).allocation_percent is None


def test_a_number_is_stored_as_itself(app):
    proj = make_project()
    member(proj, "Ann")
    row = task(proj, proj.lanes[0], "001", responsible="Ann", duration=5)
    save(client_of(app), row, allocation_percent="40")
    assert reload(row).allocation_percent == 40


def test_zero_and_a_hundred_are_both_numbers_people_mean(app):
    proj = make_project()
    member(proj, "Ann")
    lane = proj.lanes[0]
    half = task(proj, lane, "001", responsible="Ann", duration=5)
    full = task(proj, lane, "002", responsible="Ann", duration=5)
    client = client_of(app)
    save(client, half, allocation_percent="0")
    save(client, full, allocation_percent="100")
    assert reload(half).allocation_percent == 0
    assert reload(full).allocation_percent == 100


def test_the_rejected_number_comes_back_and_nothing_is_written(app):
    proj = make_project()
    member(proj, "Ann")
    row = task(proj, proj.lanes[0], "001", responsible="Ann", duration=5,
               allocation_percent=30)
    client = client_of(app)
    response = save(client, row, allocation_percent="150")
    assert response.status_code == 422
    html = response.get_data(as_text=True)
    assert "Allocation must be a whole number from 0 to 100." in html
    assert re.search(r'id="detail-allocation"[^>]*value="150"', html)
    assert reload(row).allocation_percent == 30


@pytest.mark.parametrize("bad", ["60%", "1.5", "-5", "\u00b2", "101"])
def test_nonsense_never_reaches_the_row(app, bad):
    proj = make_project()
    member(proj, "Ann")
    row = task(proj, proj.lanes[0], "001", responsible="Ann", duration=5,
               allocation_percent=30)
    response = save(client_of(app), row, allocation_percent=bad)
    assert response.status_code == 422
    assert "Allocation must be a whole number from 0 to 100." in response.get_data(as_text=True)
    assert reload(row).allocation_percent == 30


def test_progress_keeps_the_same_rule_as_the_share(app):
    proj = make_project()
    member(proj, "Ann")
    row = task(proj, proj.lanes[0], "001", responsible="Ann", duration=5, progress_percent=20)
    client = client_of(app)
    response = save(client, row, progress_percent="\u00b2")
    assert response.status_code == 422          # used to raise before the shared parse
    assert "Progress must be a whole number from 0 to 100." in response.get_data(as_text=True)
    response = save(client, row, progress_percent="150")
    assert response.status_code == 422
    assert reload(row).progress_percent == 20
    save(client, row, progress_percent="40")
    assert reload(row).progress_percent == 40


def test_a_milestone_is_not_somebodys_time(app):
    proj = make_project()
    member(proj, "Ann")
    row = task(proj, proj.lanes[0], "001", responsible="Ann", duration=0,
               kind=TASK_KIND_MILESTONE)
    client = client_of(app)
    html = modal(client, row).get_data(as_text=True)
    assert "A milestone takes no time to run" in html
    assert re.search(r'name="allocation_percent"[^>]*disabled', html) or \
        re.search(r'disabled title="A milestone takes no time to run"', html)
    # A hand-made post cannot give a milestone a share of a person.
    assert save(client, row, allocation_percent="40").status_code == 200
    assert reload(row).allocation_percent is None


def test_the_share_asks_the_capacity_tab_to_redraw(app):
    proj = make_project()
    member(proj, "Ann")
    row = task(proj, proj.lanes[0], "001", responsible="Ann", duration=5)
    response = save(client_of(app), row, allocation_percent="50")
    assert "refreshCapacity" in response.headers["HX-Trigger-After-Swap"]


def test_the_grid_does_not_ask_for_a_share_it_cannot_show(app):
    """The inline row form has no allocation field, so saving a name must not clear one."""
    proj = make_project()
    member(proj, "Ann")
    row = task(proj, proj.lanes[0], "001", responsible="Ann", duration=5,
               allocation_percent=40)
    client = client_of(app)
    response = client.post(f"/tasks/{row.id}/update", data={
        "name": "Renamed in the grid", "lane_name": "Lane", "responsible": "Ann",
        "start_date": fmt_date(MON), "duration_days": "5", "requirement_id": "",
    }, headers={"HX-Request": "true"})
    assert response.status_code == 200
    assert reload(row).allocation_percent == 40


# ---------------------------------------------------------------------------
# The share, through the capacity engine
# ---------------------------------------------------------------------------

def test_half_a_person_is_half_the_weekload(client):
    proj = make_project()
    member(proj, "Ann")
    lane = proj.lanes[0]
    task(proj, lane, "001", responsible="Ann", duration=5)
    task(proj, lane, "002", responsible="Ann", duration=5)
    assert load_of(client, proj) == "200%"

    second = Task.query.filter_by(code="002").one()
    save(client, second, allocation_percent="50")
    assert load_of(client, proj) == "150%"


def test_a_share_of_zero_asks_nothing_of_nobody(client):
    proj = make_project()
    member(proj, "Ann")
    lane = proj.lanes[0]
    task(proj, lane, "001", responsible="Ann", duration=5)
    task(proj, lane, "002", responsible="Ann", duration=5)
    second = Task.query.filter_by(code="002").one()
    save(client, second, allocation_percent="0")
    assert load_of(client, proj) == "100%"


# ---------------------------------------------------------------------------
# The grid mark
# ---------------------------------------------------------------------------

def test_a_row_marks_a_person_who_is_already_over(client):
    proj = make_project()
    member(proj, "Ann")
    lane = proj.lanes[0]
    first = task(proj, lane, "001", responsible="Ann", duration=5)
    second = task(proj, lane, "002", responsible="Ann", duration=5)

    html = page(client, proj)
    for row in (first, second):
        block = row_block(html, row)
        assert "task-over-mark" in block
        assert "&#9888;" in block                    # a shape as well as a colour
        assert "Ann is at 200% in the week of 1 Jun" in block
        # the sentence has to reach a mouse and a screen reader, not one of them
        assert 'aria-label="Ann is at 200% in the week of 1 Jun"' in block
        assert 'title="Ann is at 200% in the week of 1 Jun' in block
        assert "see the Capacity tab" in block            # it says where to go next
        assert "task-assignee-over" in block


def test_a_row_that_fits_is_left_alone(client):
    proj = make_project()
    member(proj, "Ann")
    row = task(proj, proj.lanes[0], "001", responsible="Ann", duration=5)
    block = row_block(page(client, proj), row)
    assert "task-over-mark" not in block
    assert "task-assignee" in block               # the wrapper is always there


def test_the_mark_waits_for_the_weeks_the_task_needs(client):
    proj = make_project()
    member(proj, "Ann")
    lane = proj.lanes[0]
    busy = task(proj, lane, "001", responsible="Ann", duration=5)
    also = task(proj, lane, "002", responsible="Ann", duration=5)
    later = task(proj, lane, "003", responsible="Ann", start=MON + 2 * WEEK, duration=5)

    html = page(client, proj)
    assert "task-over-mark" in row_block(html, busy)
    assert "task-over-mark" in row_block(html, also)
    assert "task-over-mark" not in row_block(html, later)


def test_a_task_with_no_dates_has_no_weeks_to_be_over_in(app):
    """A row can be undated while somebody drafts it, and has no crunch to warn about."""
    from types import SimpleNamespace

    import app.routes.context as context_module

    proj = make_project()
    member(proj, "Ann")
    lane = proj.lanes[0]
    task(proj, lane, "001", responsible="Ann", duration=5)
    task(proj, lane, "002", responsible="Ann", duration=5)
    waiting = SimpleNamespace(id=999, responsible="Ann", start_date=None, end_date=None,
                              is_milestone=False, done=False)

    rows = Task.query.filter_by(project_id=proj.id).all()
    marks = context_module.capacity_overload_map(proj, rows + [waiting], TeamMember.query.all())
    assert 999 not in marks
    assert len(marks) == 2                      # the two real, dated, over-loaded rows


def test_a_person_with_no_availability_cannot_be_overloaded(client):
    """A name outside the team has no week to exceed; the tab keeps that in a sentence."""
    proj = make_project()
    member(proj, "Ann")
    lane = proj.lanes[0]
    ghost = task(proj, lane, "001", responsible="Cleo", duration=5)
    ghost_two = task(proj, lane, "002", responsible="Cleo", duration=5)
    html = page(client, proj)
    assert "task-over-mark" not in row_block(html, ghost)
    assert "task-over-mark" not in row_block(html, ghost_two)


def test_work_with_no_owner_warns_nobody(client):
    proj = make_project()
    member(proj, "Ann")
    lane = proj.lanes[0]
    unowned = task(proj, lane, "001", responsible=None, duration=5)
    unowned_two = task(proj, lane, "002", responsible=None, duration=5)
    html = page(client, proj)
    assert "task-over-mark" not in row_block(html, unowned)
    assert "task-over-mark" not in row_block(html, unowned_two)


def test_delivered_work_is_not_marked(client):
    proj = make_project()
    member(proj, "Ann")
    lane = proj.lanes[0]
    done = task(proj, lane, "001", responsible="Ann", duration=5, done=True)
    busy = task(proj, lane, "002", responsible="Ann", duration=5)
    busy_two = task(proj, lane, "003", responsible="Ann", duration=5)

    html = page(client, proj)
    assert "task-over-mark" not in row_block(html, done)
    assert "task-over-mark" in row_block(html, busy)
    assert "task-over-mark" in row_block(html, busy_two)


def test_a_delivery_gate_is_never_marked(client):
    proj = make_project()
    member(proj, "Ann")
    lane = proj.lanes[0]
    task(proj, lane, "001", responsible="Ann", duration=5)
    task(proj, lane, "002", responsible="Ann", duration=5)
    gate = task(proj, lane, "003", responsible="Ann", duration=0, kind=TASK_KIND_MILESTONE)
    assert "task-over-mark" not in row_block(page(client, proj), gate)


def test_the_select_says_what_it_is_asking_for(client):
    proj = make_project()
    member(proj, "Ann")
    lane = proj.lanes[0]
    whole = task(proj, lane, "001", responsible="Ann", duration=5)
    half = task(proj, lane, "002", responsible="Ann", duration=5, allocation_percent=40)
    none = task(proj, lane, "003", responsible="Ann", duration=5, allocation_percent=0)

    html = page(client, proj)
    assert "title=\"Whole of this person&#39;s time\"" in row_block(html, whole)
    assert "title=\"40% of this person&#39;s time\"" in row_block(html, half)
    assert 'title="Nothing: this task asks no time of its owner"' in row_block(html, none)


def test_the_mark_names_the_week_it_found(client):
    proj = make_project()
    member(proj, "Ann")
    lane = proj.lanes[0]
    for week in range(3):
        task(proj, lane, f"00{week + 1}", responsible="Ann", duration=5, start=MON + week * WEEK)
    for week in range(3):
        task(proj, lane, f"10{week}", responsible="Ann", duration=5, start=MON + week * WEEK)

    html = page(client, proj)
    block = row_block(html, Task.query.filter_by(code="101").one())
    assert "Ann is at 200% in the week of 8 Jun" in block     # no leading zero


def test_the_map_is_a_dict_of_task_id_to_the_worst_week_found(client, app):
    proj = make_project()
    member(proj, "Ann")
    lane = proj.lanes[0]
    busy = task(proj, lane, "001", responsible="Ann", duration=5)
    task(proj, lane, "002", responsible="Ann", duration=5)
    db.session.expire_all()

    marks = capacity_overload_map(
        proj, Task.query.filter_by(project_id=proj.id).all(), TeamMember.query.all())
    assert set(marks) == {busy.id, Task.query.filter_by(code="002").one().id}
    mark = marks[busy.id]
    assert mark["name"] == "Ann"
    assert mark["percent"] == "200%"
    assert mark["week"] == "1 Jun"
    assert mark["text"] == "Ann is at 200% in the week of 1 Jun"


def test_the_map_costs_nothing_when_nothing_could_be_marked(app, monkeypatch):
    """The grid renders on every inline edit, so an empty team must not pay for a build."""
    import app.routes.context as context_module

    def no_build(*args, **kwargs):
        raise AssertionError("capacity was computed for a grid that has nothing to mark")

    monkeypatch.setattr(context_module, "build_capacity", no_build)

    proj = make_project()
    lane = proj.lanes[0]
    task(proj, lane, "001", responsible="Cleo", duration=5)      # no members at all
    unowned = task(proj, lane, "002", responsible=None, duration=5)
    assert context_module.capacity_overload_map(proj, [unowned], []) == {}

    member(proj, "Ann")
    assert context_module.capacity_overload_map(proj, [unowned], TeamMember.query.all()) == {}

    cleo = task(proj, lane, "003", responsible="Cleo", duration=5)
    assert context_module.capacity_overload_map(
        proj, [cleo], TeamMember.query.all()) == {}       # a ghost owns no availability


def test_the_grid_builds_the_weeks_once_per_render(app, monkeypatch):
    """Marking a hundred rows must not mean computing a hundred grids."""
    import app.routes.context as context_module

    real = context_module.build_capacity
    calls = []

    def counting(*args, **kwargs):
        calls.append(1)
        return real(*args, **kwargs)

    monkeypatch.setattr(context_module, "build_capacity", counting)
    proj = make_project()
    member(proj, "Ann")
    lane = proj.lanes[0]
    for code in ("001", "002", "003", "004"):
        task(proj, lane, code, responsible="Ann", duration=5)

    client_of(app).get(f"/projects/{proj.id}")
    assert len(calls) == 1

