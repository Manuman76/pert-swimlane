"""Lanes are managed inside the task grid - there is no Lanes panel any more.

The Lane cell is a free-text combo (`<input name="lane_name" list=...>` plus a
datalist of the project's lanes), so:

* picking a name from the list assigns that lane;
* typing an unknown name creates the lane on save;
* clearing the cell keeps the task where it is (a blank must never orphan it);
* a lane left without tasks is pruned, because nothing exists to clean it up;
* the cell's up/down arrows reorder the swimlane the task belongs to.

`lane_id` (the old dropdown) is still accepted by the endpoint, so the lane
routes and any scripted client keep working.
"""
import re
from datetime import date

import pytest

from app import create_app
from app.db import db
from app.models import Lane, Project, Task


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


def _project_with_task(name="Grid", lane_name="Backend", start=date(2017, 6, 1)):
    proj = Project(name=name)
    db.session.add(proj)
    db.session.commit()
    lane = Lane(project_id=proj.id, name=lane_name, sort_order=1)
    db.session.add(lane)
    db.session.commit()
    task = Task(project_id=proj.id, code="001", lane_id=lane.id, name="T1",
                start_date=start, duration_days=2)
    db.session.add(task)
    db.session.commit()
    return proj, lane, task


def _lane_names(project_id):
    return [l.name for l in
            Lane.query.filter_by(project_id=project_id).order_by(Lane.sort_order).all()]


def _add_task(proj, lane, code, name="T", start=date(2017, 6, 1), dur=2):
    task = Task(project_id=proj.id, code=code, lane_id=lane.id, name=name,
                start_date=start, duration_days=dur)
    db.session.add(task)
    db.session.commit()
    return task


def _update(client, task_id, lane_name=None, lane_id=None, **fields):
    """Post a grid edit, keeping the other fields at their saved values."""
    data = {"name": fields.pop("name", "T1"),
            "start_date": fields.pop("start_date", "1 Jun 2017"),
            "duration_days": str(fields.pop("duration_days", 2))}
    if lane_name is not None:
        data["lane_name"] = lane_name
    if lane_id is not None:
        data["lane_id"] = str(lane_id)
    data.update(fields)
    return client.post(f"/tasks/{task_id}/update", data=data)


# ---------------------------------------------------------------------------
# the grid's Lane cell
# ---------------------------------------------------------------------------

def test_lanes_panel_is_gone_and_cell_is_a_combo(client, app):
    with app.app_context():
        proj, lane, task = _project_with_task()
        pid, tid = proj.id, task.id

    html = client.get(f"/projects/{pid}").get_data(as_text=True)

    # No lane manager panel at all...
    assert "<strong>Lanes</strong>" not in html
    assert 'class="lane-list"' not in html
    # ...and the Lane cell is now a text combo backed by the project's lanes.
    assert f'name="lane_name" value="Backend"' in html
    assert f'list="lane-options-{tid}"' in html
    assert '<datalist id="lane-options-' in html
    # The lane is no longer a dropdown; only the order arrows send a lane id.
    assert 'name="lane_id"' not in html.replace('name="lane_id" value=', '')


def test_picking_an_existing_lane_moves_the_task(client, app):
    with app.app_context():
        proj, backend, t1 = _project_with_task()
        extra = Lane(project_id=proj.id, name="Frontend", sort_order=2)
        db.session.add(extra)
        db.session.commit()
        # A second task stays behind in Backend so the lane is not pruned empty:
        # this test is about *selecting* a lane, not about the pruning rule.
        _add_task(proj, backend, "002", "T2")
        t1_id, other_id, pid = t1.id, extra.id, proj.id

    resp = _update(client, t1_id, lane_name="Frontend")
    assert resp.status_code == 200

    with app.app_context():
        assert Task.query.get(t1_id).lane_id == other_id
        # Nothing was created: the typed name matched an existing lane.
        assert _lane_names(pid) == ["Backend", "Frontend"]


def test_typing_an_unknown_lane_creates_it(client, app):
    with app.app_context():
        proj, lane, task = _project_with_task()
        _add_task(proj, lane, "002", "T2")
        pid, tid = proj.id, task.id

    resp = client.post(f"/tasks/{tid}/update", data={
        "name": "T1", "lane_name": "QA",
        "start_date": "1 Jun 2017", "duration_days": "2",
    })
    assert resp.status_code == 200

    with app.app_context():
        assert _lane_names(pid) == ["Backend", "QA"]        # appended in order
        new = Lane.query.filter_by(project_id=pid, name="QA").one()
        assert Task.query.get(tid).lane_id == new.id
        # The created lane is offered by the row's list straight away.
        assert '<option value="QA">' in resp.get_data(as_text=True)


def test_lane_name_match_is_case_insensitive(client, app):
    """Retyping a lane in another case reuses it and adopts the typed spelling,
    rather than creating a duplicate swimlane."""
    with app.app_context():
        proj, lane, task = _project_with_task()
        pid, tid, lid = proj.id, task.id, lane.id

    client.post(f"/tasks/{tid}/update", data={
        "name": "T1", "lane_name": "backend",
        "start_date": "1 Jun 2017", "duration_days": "2",
    })

    with app.app_context():
        assert Lane.query.filter_by(project_id=pid).count() == 1
        assert Lane.query.get(lid).name == "backend"
        assert Task.query.get(tid).lane_id == lid


def test_blank_lane_cell_keeps_the_current_lane(client, app):
    with app.app_context():
        proj, lane, task = _project_with_task()
        pid, tid, lid = proj.id, task.id, lane.id

    resp = _update(client, tid, lane_name="")
    assert resp.status_code == 200

    with app.app_context():
        assert Task.query.get(tid).lane_id == lid
        assert _lane_names(pid) == ["Backend"]


def test_emptying_a_lane_prunes_it(client, app):
    """Renaming the only task of a lane moves the lane out of existence -
    the stale name must not linger in every row's list."""
    with app.app_context():
        proj, backend, task = _project_with_task()
        other = Task(project_id=proj.id, code="002", lane_id=backend.id, name="T2",
                     start_date=date(2017, 6, 1), duration_days=2)
        db.session.add(other)
        db.session.commit()
        pid, tid = proj.id, task.id

    # 001 leaves Backend for a new lane, but 002 still uses it: kept.
    client.post(f"/tasks/{tid}/update", data={
        "name": "T1", "lane_name": "QA",
        "start_date": "1 Jun 2017", "duration_days": "2",
    })
    with app.app_context():
        assert _lane_names(pid) == ["Backend", "QA"]

    # Now 002 leaves too: Backend has no tasks left and is dropped.
    t2 = Task.query.filter_by(project_id=pid, code="002").first()
    client.post(f"/tasks/{t2.id}/update", data={
        "name": "T2", "lane_name": "QA",
        "start_date": "1 Jun 2017", "duration_days": "2",
    })
    with app.app_context():
        assert _lane_names(pid) == ["QA"]


def test_lane_id_field_is_still_accepted(client, app):
    """Backward compatibility: an older client posting the dropdown value still
    assigns the lane and never creates a new one."""
    with app.app_context():
        proj, lane, task = _project_with_task()
        new = Lane(project_id=proj.id, name="Frontend", sort_order=2)
        db.session.add(new)
        db.session.commit()
        _add_task(proj, lane, "002", "T2")
        pid, tid, lid = proj.id, task.id, new.id

    resp = client.post(f"/tasks/{tid}/update", data={
        "name": "T1", "lane_id": str(lid),
        "start_date": "1 Jun 2017", "duration_days": "2",
    })
    assert resp.status_code == 200

    with app.app_context():
        assert Task.query.get(tid).lane_id == lid
        assert _lane_names(pid) == ["Backend", "Frontend"]


def test_no_lane_error_blocks_the_save(client, app):
    """A rejected edit does not create the lane that was typed."""
    with app.app_context():
        proj, lane, task = _project_with_task()
        pid, tid = proj.id, task.id

    resp = client.post(f"/tasks/{tid}/update", data={
        "name": "T1", "lane_name": "NeverCreated",
        "start_date": "1 Jun 2017", "duration_days": "0",   # invalid: < 1
    })
    assert resp.status_code == 422

    with app.app_context():
        assert _lane_names(pid) == ["Backend"]
        # The typed value is echoed back in the row so the user can fix it.
        assert 'name="lane_name" value="NeverCreated"' in resp.get_data(as_text=True)


# ---------------------------------------------------------------------------
# swimlane order, from the arrows next to the Lane field
# ---------------------------------------------------------------------------

def _two_lanes_with_tasks():
    """Backend (first) with task 001, QA (second) with task 002."""
    proj, backend, t1 = _project_with_task()
    qa = Lane(project_id=proj.id, name="QA", sort_order=2)
    db.session.add(qa)
    db.session.commit()
    t2 = _add_task(proj, qa, "002", "T2")
    return proj, backend, qa, t1, t2


def test_lane_cell_offers_order_arrows(client, app):
    with app.app_context():
        proj, backend, qa, t1, t2 = _two_lanes_with_tasks()
        pid = proj.id

    html = client.get(f"/projects/{pid}").get_data(as_text=True)

    assert html.count('value="move_up"') == 2
    assert html.count('value="move_down"') == 2
    assert 'name="partial" value="grid"' in html
    # Backend is the first swimlane and QA the last: the outer arrows are dead.
    up_off = re.findall(r'title="Move lane &quot;(.+?)&quot; up one swimlane"\s+disabled', html)
    down_off = re.findall(r'title="Move lane &quot;(.+?)&quot; down one swimlane"\s+disabled', html)
    assert up_off == ["Backend"]
    assert down_off == ["QA"]


def test_arrows_move_the_whole_lane_and_refresh_the_grid(client, app):
    with app.app_context():
        proj, backend, qa, t1, t2 = _two_lanes_with_tasks()
        pid, qa_id, t2_id = proj.id, qa.id, t2.id

    resp = client.post(f"/projects/{pid}/lanes", data={
        "lane_id": str(qa_id), "action": "move_up", "partial": "grid",
    })
    assert resp.status_code == 200
    # The grid asks for the rows back, and the diagram redraws in the new order.
    assert resp.headers["HX-Trigger-After-Swap"] == "refreshDiagram"
    assert 'id="task-row-' in resp.get_data(as_text=True)

    with app.app_context():
        assert _lane_names(pid) == ["QA", "Backend"]

    labels = re.findall(r'class="lane-name">([^<]+)<',
                        client.get(f"/projects/{pid}/diagram").get_data(as_text=True))
    assert labels == ["QA", "Backend"]


def test_arrows_disabled_state_follows_the_new_order(client, app):
    with app.app_context():
        proj, backend, qa, t1, t2 = _two_lanes_with_tasks()
        pid, qa_id = proj.id, qa.id

    client.post(f"/projects/{pid}/lanes", data={"lane_id": str(qa_id),
                                                 "action": "move_up", "partial": "grid"})
    html = client.get(f"/projects/{pid}").get_data(as_text=True)

    assert re.findall(r'title="Move lane &quot;(.+?)&quot; up one swimlane"\s+disabled', html) == ["QA"]
    assert re.findall(r'title="Move lane &quot;(.+?)&quot; down one swimlane"\s+disabled', html) == ["Backend"]


def test_a_new_lane_lands_last_and_can_be_moved_up(client, app):
    with app.app_context():
        proj, backend, qa, t1, t2 = _two_lanes_with_tasks()
        pid, t1_id = proj.id, t1.id

    # 001 leaves Backend for a brand new lane; Backend is emptied and pruned, so
    # the lanes are QA then Delivery.
    _update(client, t1_id, lane_name="Delivery")
    with app.app_context():
        assert _lane_names(pid) == ["QA", "Delivery"]
        delivery_id = Lane.query.filter_by(project_id=pid, name="Delivery").one().id

    client.post(f"/projects/{pid}/lanes", data={"lane_id": str(delivery_id),
                                                "action": "move_up", "partial": "grid"})
    with app.app_context():
        assert _lane_names(pid) == ["Delivery", "QA"]


def test_lane_route_still_redirects_without_the_partial_flag(client, app):
    """Plain form posts (older clients, scripts, tests) keep the page redirect."""
    with app.app_context():
        proj, backend, qa, t1, t2 = _two_lanes_with_tasks()
        pid, qa_id = proj.id, qa.id

    resp = client.post(f"/projects/{pid}/lanes", data={"lane_id": str(qa_id), "action": "move_up"})
    assert resp.status_code == 302


# ---------------------------------------------------------------------------
# a project with no lanes at all
# ---------------------------------------------------------------------------

def test_first_task_seeds_a_lane(client, app):
    with app.app_context():
        proj = Project(name="Fresh")
        db.session.add(proj)
        db.session.commit()
        pid = proj.id

    assert Lane.query.filter_by(project_id=pid).count() == 0
    resp = client.post(f"/projects/{pid}/tasks", data={})
    assert resp.status_code == 200

    with app.app_context():
        assert _lane_names(pid) == ["Lane 1"]
        assert Task.query.filter_by(project_id=pid).one().lane.name == "Lane 1"


def test_add_task_continues_the_last_lane(client, app):
    with app.app_context():
        proj, lane, task = _project_with_task(lane_name="Backend")
        qa = Lane(project_id=proj.id, name="QA", sort_order=2)
        db.session.add(qa)
        db.session.commit()
        pid = proj.id

    client.post(f"/projects/{pid}/tasks", data={})
    client.post(f"/projects/{pid}/tasks", data={"lane_name": "Docs"})

    with app.app_context():
        rows = Task.query.filter_by(project_id=pid).order_by(Task.code).all()
        # No lane chosen: the row continues the lane of the last row (Backend).
        assert rows[1].lane.name == "Backend"
        # An explicit name is honoured by the add endpoint too.
        assert rows[2].lane.name == "Docs"
        assert _lane_names(pid) == ["Backend", "QA", "Docs"]
