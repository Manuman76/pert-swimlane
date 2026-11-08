"""Tests for Diagram features: Critical Path, Filtering, Collapsible Lanes, and Zoom/Pan."""

from datetime import date
import pytest

from app import create_app
from app.db import db
from app.models import Project, Lane, Task, TASK_KIND_MILESTONE, Requirement
from app.diagram import build_diagram
from app.services.scheduler import compute_critical_path


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


def test_critical_path_linear_sequence(app):
    """Linear sequence A -> B -> C: all tasks and edges are critical."""
    with app.app_context():
        proj = Project(name="CP Linear")
        db.session.add(proj)
        db.session.flush()
        lane = Lane(project_id=proj.id, name="Dev", sort_order=1)
        db.session.add(lane)
        db.session.flush()

        t1 = Task(project_id=proj.id, code="001", lane_id=lane.id, name="A", start_date=date(2026, 11, 2), duration_days=3)
        t2 = Task(project_id=proj.id, code="002", lane_id=lane.id, name="B", start_date=date(2026, 11, 5), duration_days=4, predecessors="001")
        t3 = Task(project_id=proj.id, code="003", lane_id=lane.id, name="C", start_date=date(2026, 11, 11), duration_days=2, predecessors="002")
        db.session.add_all([t1, t2, t3])
        db.session.commit()

        tasks = [t1, t2, t3]
        critical_tasks, critical_edges = compute_critical_path(tasks)

        assert critical_tasks == {"001", "002", "003"}
        assert ("001", "002") in critical_edges
        assert ("002", "003") in critical_edges


def test_critical_path_parallel_branches(app):
    """Parallel branches:
    A (start) -> B (long: 10d) -> D (end)
    A (start) -> C (short: 2d)  -> D (end)
    Branch A-B-D must be critical; C must have float and NOT be critical.
    """
    with app.app_context():
        proj = Project(name="CP Parallel")
        db.session.add(proj)
        db.session.flush()
        l1 = Lane(project_id=proj.id, name="Dev", sort_order=1)
        db.session.add(l1)
        db.session.flush()

        # A: Mon Nov 2, dur 2d -> Wed Nov 4
        ta = Task(project_id=proj.id, code="A", lane_id=l1.id, name="Start", start_date=date(2026, 11, 2), duration_days=2)
        # B: Wed Nov 4, dur 10d -> finishes Nov 18
        tb = Task(project_id=proj.id, code="B", lane_id=l1.id, name="Long Branch", start_date=date(2026, 11, 4), duration_days=10, predecessors="A")
        # C: Wed Nov 4, dur 2d -> finishes Nov 6 (has float!)
        tc = Task(project_id=proj.id, code="C", lane_id=l1.id, name="Short Branch", start_date=date(2026, 11, 4), duration_days=2, predecessors="A")
        # D: Wed Nov 18, dur 2d -> finishes Nov 20
        td = Task(project_id=proj.id, code="D", lane_id=l1.id, name="Merge", start_date=date(2026, 11, 18), duration_days=2, predecessors="B,C")
        db.session.add_all([ta, tb, tc, td])
        db.session.commit()

        tasks = [ta, tb, tc, td]
        critical_tasks, critical_edges = compute_critical_path(tasks)

        assert "A" in critical_tasks
        assert "B" in critical_tasks
        assert "D" in critical_tasks
        assert "C" not in critical_tasks  # Task C has 8 days of slack

        assert ("A", "B") in critical_edges
        assert ("B", "D") in critical_edges
        assert ("A", "C") not in critical_edges
        assert ("C", "D") not in critical_edges


def test_diagram_renders_toolbar_and_zoom_controls(app):
    with app.app_context():
        proj = Project(name="Toolbar Proj")
        db.session.add(proj)
        db.session.flush()
        l1 = Lane(project_id=proj.id, name="Infra", sort_order=1)
        db.session.add(l1)
        db.session.flush()
        t1 = Task(project_id=proj.id, code="001", lane_id=l1.id, name="Setup", start_date=date(2026, 11, 2), duration_days=3)
        db.session.add(t1)
        db.session.commit()

        html = build_diagram([l1], [t1])

        # Filters
        assert 'id="diagram-filter-lane"' in html
        assert 'id="diagram-filter-status"' in html
        assert 'id="diagram-filter-responsible"' in html
        assert 'id="diagram-filter-critical"' not in html
        assert 'id="diagram-filter-hide-completed"' in html
        assert 'id="diagram-filter-show-requirements"' in html

        # Zoom controls
        assert 'id="btn-zoom-in"' in html
        assert 'id="btn-zoom-out"' in html
        assert 'id="btn-zoom-reset"' in html
        assert 'id="btn-zoom-fit"' in html
        assert 'id="btn-zoom-center"' in html
        assert 'id="btn-toggle-minimap"' in html

        # Viewport & stage & minimap
        assert 'id="diagram-viewport"' in html
        assert 'id="diagram-stage"' in html
        assert 'id="diagram-minimap"' in html
        assert 'id="minimap-canvas"' in html


def test_diagram_collapsible_swimlane(app):
    with app.app_context():
        import re

        proj = Project(name="Collapse Proj")
        db.session.add(proj)
        db.session.flush()
        l1 = Lane(project_id=proj.id, name="Infrastructure", sort_order=1)
        l2 = Lane(project_id=proj.id, name="Development", sort_order=2)
        db.session.add_all([l1, l2])
        db.session.flush()

        t1 = Task(project_id=proj.id, code="001", lane_id=l1.id, name="Servers", start_date=date(2026, 11, 2), duration_days=3)
        t2 = Task(project_id=proj.id, code="002", lane_id=l2.id, name="Coding", start_date=date(2026, 11, 5), duration_days=4, predecessors="001")
        db.session.add_all([t1, t2])
        db.session.commit()

        lanes = [l1, l2]
        tasks = [t1, t2]

        # 1. Expanded diagram (default)
        html_expanded = build_diagram(lanes, tasks)
        assert "Infrastructure" in html_expanded
        assert "Development" in html_expanded
        assert "&#9660;" in html_expanded  # ▼ expanded symbol
        assert "1 task" in html_expanded

        # 2. Collapse Lane 1 (Infrastructure)
        html_collapsed = build_diagram(lanes, tasks, collapsed_lanes={l1.id})
        assert "lane-collapsed" in html_collapsed
        assert "&#9654;" in html_collapsed  # ▶ collapsed symbol

        # Height of collapsed lane is 42px
        heights = [int(h) for h in re.findall(r'height:(\d+)px;', html_collapsed)]
        assert 42 in heights

        # Task 001 is inside collapsed lane and omitted from positions/cards on canvas
        # Task 002 in Development is visible
        assert "Coding" in html_collapsed
        assert "Servers" not in html_collapsed


def test_diagram_partial_with_collapsed_query_param(client, app):
    with app.app_context():
        proj = Project(name="Collapse Route")
        db.session.add(proj)
        db.session.flush()
        l1 = Lane(project_id=proj.id, name="Design", sort_order=1)
        l2 = Lane(project_id=proj.id, name="Code", sort_order=2)
        db.session.add_all([l1, l2])
        db.session.flush()

        t1 = Task(project_id=proj.id, code="001", lane_id=l1.id, name="Mockup", start_date=date(2026, 11, 2), duration_days=2)
        t2 = Task(project_id=proj.id, code="002", lane_id=l2.id, name="Impl", start_date=date(2026, 11, 4), duration_days=3, predecessors="001")
        db.session.add_all([t1, t2])
        db.session.commit()
        pid = proj.id
        l1_id = l1.id

    resp = client.get(f"/projects/{pid}/diagram?collapsed={l1_id}")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "lane-collapsed" in html
    assert "&#9654;" in html


def test_diagram_shows_requirements_badges_when_linked(app):
    with app.app_context():
        proj = Project(name="Req Badge Proj")
        db.session.add(proj)
        db.session.flush()
        l1 = Lane(project_id=proj.id, name="Core", sort_order=1)
        db.session.add(l1)
        db.session.flush()

        req = Requirement(project_id=proj.id, code="BR-001", title="SSO Login", moscow="must")
        t = Task(project_id=proj.id, code="001", lane_id=l1.id, name="OAuth Flow", start_date=date(2026, 11, 2), duration_days=3)
        req.tasks = [t]
        db.session.add_all([req, t])
        db.session.commit()

        html = build_diagram([l1], [t])
        assert "card-requirements-row" in html
        assert "BR-001" in html
