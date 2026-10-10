import pytest
from datetime import date, timedelta
from app import create_app
from app.db import db
from app.models import Project, Lane, Task


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


def _create_project(name):
    proj = Project(name=name)
    db.session.add(proj)
    db.session.commit()
    return proj


def _create_lane(proj, name, sort_order=1):
    lane = Lane(project_id=proj.id, name=name, sort_order=sort_order)
    db.session.add(lane)
    db.session.commit()
    return lane


# Unique project name counter for test isolation
_project_counter = 0


def test_end_date(app):
    with app.app_context():
        proj = _create_project("TestEndDate")
        lane = _create_lane(proj, "Lane1")
        task = Task(
            project_id=proj.id,
            code="001",
            lane_id=lane.id,
            name="T1",
            start_date=date(2017, 6, 1),
            duration_days=3,
        )
        assert task.end_date == date(2017, 6, 4)


def test_code_auto_assignment(client, app):
    with app.app_context():
        proj = _create_project("P1")
        lane = _create_lane(proj, "L")

        resp1 = client.post(f"/projects/{proj.id}/tasks", data={"lane_id": lane.id})
        assert resp1.status_code == 200
        resp2 = client.post(f"/projects/{proj.id}/tasks", data={"lane_id": lane.id})
        assert resp2.status_code == 200

        tasks = Task.query.filter_by(project_id=proj.id).order_by(Task.code).all()
        assert [t.code for t in tasks] == ["001", "002"]


def test_validation_unknown_predecessor(client, app):
    with app.app_context():
        proj = _create_project("P2")
        lane = _create_lane(proj, "L")
        task = Task(
            project_id=proj.id,
            code="001",
            lane_id=lane.id,
            name="T1",
            start_date=date(2017, 6, 1),
            duration_days=3,
        )
        db.session.add(task)
        db.session.commit()

        resp = client.post(f"/tasks/{task.id}/update", data={
            "predecessors": "999",
            "start_date": "1 Jun 2017",
            "duration_days": "3",
        })
        assert resp.status_code == 422


def test_validation_cycle(client, app):
    with app.app_context():
        proj = _create_project("P3")
        lane = _create_lane(proj, "L")
        t1 = Task(project_id=proj.id, code="001", lane_id=lane.id, name="T1", start_date=date(2017, 6, 1), duration_days=3)
        t2 = Task(project_id=proj.id, code="002", lane_id=lane.id, name="T2", start_date=date(2017, 6, 5), duration_days=3)
        db.session.add_all([t1, t2])
        db.session.commit()

        # Set 002's predecessor to 001 to form a potential cycle
        t2.predecessors = "001"
        db.session.commit()

        # Now 001 -> 002 would create a cycle: 001 -> 002 -> 001
        resp = client.post(f"/tasks/{t1.id}/update", data={
            "predecessors": "002",
            "start_date": "10 Jun 2017",
            "duration_days": "3",
        })
        assert resp.status_code == 422


def test_validation_scheduling(client, app):
    with app.app_context():
        proj = _create_project("P4")
        lane = _create_lane(proj, "L")
        t1 = Task(project_id=proj.id, code="001", lane_id=lane.id, name="T1", start_date=date(2017, 6, 1), duration_days=3)
        db.session.add(t1)
        db.session.commit()

        t2 = Task(project_id=proj.id, code="002", lane_id=lane.id, name="T2", start_date=date(2017, 6, 5), duration_days=1)
        db.session.add(t2)
        db.session.commit()

        # T2 starts before T1 ends (T1 ends 2017-06-04, T2 starts 2017-06-02)
        resp = client.post(f"/tasks/{t2.id}/update", data={
            "predecessors": "001",
            "start_date": "2 Jun 2017",
            "duration_days": "1",
        })
        assert resp.status_code == 422


def test_layout_flow_to_the_right(app):
    """Every task must be drawn strictly to the right of all of its predecessors,
    and a lane's tasks must be indented further right than its predecessor lane's tasks."""
    with app.app_context():
        import re
        from app.diagram import build_diagram, CARD_W

        proj = _create_project("P6")
        l1 = _create_lane(proj, "L1", sort_order=1)
        l2 = _create_lane(proj, "L2", sort_order=2)
        l3 = _create_lane(proj, "L3", sort_order=3)
        data = [
            # (code, lane_id, name, start, dur, preds)
            ("001", l1.id, "A", date(2017, 6, 1), 3, ""),
            ("002", l2.id, "B", date(2017, 6, 1), 8, "001"),
            ("003", l2.id, "C", date(2017, 6, 9), 15, "002"),
            ("004", l2.id, "D", date(2017, 6, 24), 7, "003"),
            ("005", l3.id, "E", date(2017, 6, 9), 8, "002"),
            ("006", l3.id, "F", date(2017, 6, 17), 6, "005"),
            ("007", l3.id, "G", date(2017, 7, 3), 2, "004,006"),
        ]
        for code, lane_id, name, start, dur, preds in data:
            db.session.add(Task(project_id=proj.id, code=code, lane_id=lane_id,
                                name=name, start_date=start, duration_days=dur,
                                predecessors=preds or None))
        db.session.commit()

        lanes = [l1, l2, l3]
        tasks = {c: Task.query.filter_by(project_id=proj.id, code=c).one()
                 for c, _, _, _, _, _ in data}
        html = build_diagram(lanes, [t for _, t in tasks.items()])

        # Card cards are rendered lane-by-lane in (start_date, code) order.
        cards = [(float(x), float(y)) for x, y
                 in re.findall(r'class="task-card" style="left:([\d.]+)px; top:([\d.]+)px;', html)]
        order = []
        for lane in lanes:
            lane_tasks = sorted((t for t in tasks.values() if t.lane_id == lane.id),
                                key=lambda t: (t.start_date, t.code))
            order.extend(lane_tasks)
        assert len(cards) == len(order)
        pos = {t.code: x for t, (x, _y) in zip(order, cards)}

        for code, task in tasks.items():
            for pcode in task.predecessor_codes():
                if pcode not in pos:
                    continue
                # Strictly to the right (no left-edge overlap).
                assert pos[code] > pos[pcode] + CARD_W, \
                    f"{pcode} -> {code}: successor not strictly right of predecessor"


def test_layout_siblings_stack_vertically(app):
    """Tasks in the same lane sharing the same predecessor set must share a
    column and stack vertically, so the shared predecessor's arrows do not cross
    a sibling card."""
    with app.app_context():
        import re
        from app.diagram import build_diagram

        proj = _create_project("P7")
        l1 = _create_lane(proj, "L1", sort_order=1)
        l2 = _create_lane(proj, "L2", sort_order=2)
        data = [
            ("001", l1.id, "Prep", date(2017, 6, 1), 4, ""),
            ("002", l2.id, "Frontend", date(2017, 6, 5), 5, "001"),
            ("003", l2.id, "Backend", date(2017, 6, 5), 5, "001"),
        ]
        for code, lane_id, name, start, dur, preds in data:
            db.session.add(Task(project_id=proj.id, code=code, lane_id=lane_id,
                                name=name, start_date=start, duration_days=dur,
                                predecessors=preds or None))
        db.session.commit()

        lanes = [l1, l2]
        tasks = {c: Task.query.filter_by(project_id=proj.id, code=c).one()
                 for c, _, _, _, _, _ in data}
        html = build_diagram(lanes, [t for _, t in tasks.items()])

        cards = {}
        for code, (x, y) in zip(
            [c for c, _, _, _, _, _ in data],
            re.findall(r'class="task-card" style="left:([\d.]+)px; top:([\d.]+)px;', html),
        ):
            cards[code] = (float(x), float(y))

        # Siblings share a column (same x) but occupy different rows (y).
        assert cards["002"][0] == cards["003"][0]
        assert cards["002"][1] != cards["003"][1]
        # Predecessor still sits strictly to the left.
        assert cards["002"][0] > cards["001"][0]
        assert cards["003"][0] > cards["001"][0]


def test_layout_lane_grows_for_stacked_siblings(app):
    """A lane that stacks siblings must be tall enough to fit every row."""
    with app.app_context():
        import re
        from app.diagram import build_diagram, CARD_H, LANE_H

        proj = _create_project("P8")
        l1 = _create_lane(proj, "L1", sort_order=1)
        l2 = _create_lane(proj, "L2", sort_order=2)
        data = [
            ("001", l1.id, "Prep", date(2017, 6, 1), 4, ""),
            ("002", l2.id, "Frontend", date(2017, 6, 5), 5, "001"),
            ("003", l2.id, "Backend", date(2017, 6, 5), 5, "001"),
        ]
        for code, lane_id, name, start, dur, preds in data:
            db.session.add(Task(project_id=proj.id, code=code, lane_id=lane_id,
                                name=name, start_date=start, duration_days=dur,
                                predecessors=preds or None))
        db.session.commit()

        lanes = [l1, l2]
        tasks = [Task.query.filter_by(project_id=proj.id, code=c).one()
                 for c, _, _, _, _, _ in data]
        html = build_diagram(lanes, tasks)

        heights = [int(h) for h in re.findall(r'class="lane-band [^"]*"[^>]*height:(\d+)px;', html)]
        assert heights[0] == LANE_H
        # Two stacked cards need at least 2 * CARD_H of vertical room.
        assert heights[1] >= 2 * CARD_H


def test_layout_coordinates(app):
    with app.app_context():
        proj = _create_project("P5")
        lane = _create_lane(proj, "L")
        task = Task(project_id=proj.id, code="001", lane_id=lane.id, name="T1", start_date=date(2017, 6, 1), duration_days=3)
        db.session.add(task)
        db.session.commit()

        from app.diagram import build_diagram
        html = build_diagram([lane], [task])
        assert "task-card" in html
        assert "001" in html
