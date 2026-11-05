"""RAID log: risks, assumptions, issues, decisions."""

from datetime import date

import pytest

from app import create_app
from app.db import db
from app.models import (
    RAID_KIND_RISK,
    RAID_KIND_ISSUE,
    RAID_KIND_DECISION,
    RaidItem,
    TeamMember,
)
from tests.test_app import _create_lane, _create_project


def _add_member(project, name):
    m = TeamMember(project_id=project.id, name=name)
    db.session.add(m)
    db.session.commit()
    return m


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


def test_raid_tab_on_project_page(client, app):
    with app.app_context():
        proj = _create_project("RAID Demo")
        pid = proj.id

    resp = client.get(f"/projects/{pid}")
    html = resp.get_data(as_text=True)
    assert "RAID log" in html
    assert 'id="raid-tab"' in html
    assert "Risks" in html
    assert "Assumptions" in html


def test_add_and_update_risk(client, app):
    with app.app_context():
        proj = _create_project("RAID Risk")
        _add_member(proj, "Eric")
        pid = proj.id

    resp = client.post(f"/projects/{pid}/raid", data={
        "action": "add",
        "kind": "risk",
        "title": "SFTP firewall approval may delay SIT",
        "probability": "high",
        "impact": "high",
        "partial": "raid",
    })
    assert resp.status_code == 200
    assert "R-001" in resp.get_data(as_text=True)

    with app.app_context():
        item = RaidItem.query.filter_by(project_id=pid, kind=RAID_KIND_RISK).one()
        assert item.probability == "high"
        rid = item.id

    resp = client.post(f"/projects/{pid}/raid", data={
        "action": "update",
        "raid_id": rid,
        "title": "SFTP firewall approval may delay SIT",
        "probability": "high",
        "impact": "high",
        "owner": "Eric",
        "mitigation": "Submit request before architecture sign-off",
        "status": "open",
        "partial": "raid",
    })
    assert resp.status_code == 200

    with app.app_context():
        item = RaidItem.query.get(rid)
        assert item.owner == "Eric"
        assert "Submit request" in item.mitigation


def test_add_issue_with_due_date(client, app):
    with app.app_context():
        proj = _create_project("RAID Issue")
        _add_member(proj, "David")
        pid = proj.id

    resp = client.post(f"/projects/{pid}/raid", data={
        "action": "add",
        "kind": "issue",
        "title": "Test environment unavailable",
        "severity": "high",
        "partial": "raid",
    })
    assert resp.status_code == 200
    assert "I-001" in resp.get_data(as_text=True)

    with app.app_context():
        item = RaidItem.query.filter_by(project_id=pid, kind=RAID_KIND_ISSUE).one()
        iid = item.id

    resp = client.post(f"/projects/{pid}/raid", data={
        "action": "update",
        "raid_id": iid,
        "title": "Test environment unavailable",
        "severity": "high",
        "owner": "David",
        "due_date": "6 Nov 2026",
        "status": "open",
        "partial": "raid",
    })
    assert resp.status_code == 200

    with app.app_context():
        item = RaidItem.query.get(iid)
        assert item.due_date == date(2026, 11, 6)


def test_decision_and_assumption_codes(client, app):
    with app.app_context():
        proj = _create_project("RAID DA")
        pid = proj.id

    client.post(f"/projects/{pid}/raid", data={
        "action": "add",
        "kind": "decision",
        "title": "Use SFTP rather than HTTPS inbound interface",
        "partial": "raid",
    })
    client.post(f"/projects/{pid}/raid", data={
        "action": "add",
        "kind": "assumption",
        "title": "Vendor provides SFTP credentials on time",
        "description": "By end of discovery",
        "partial": "raid",
    })

    with app.app_context():
        codes = {i.code for i in RaidItem.query.filter_by(project_id=pid).all()}
        assert "D-001" in codes
        assert "A-001" in codes


def test_delete_raid_item(client, app):
    with app.app_context():
        proj = _create_project("RAID Del")
        pid = proj.id
        item = RaidItem(
            project_id=pid,
            kind=RAID_KIND_DECISION,
            code="D-001",
            title="Temporary",
        )
        db.session.add(item)
        db.session.commit()
        rid = item.id

    resp = client.post(f"/projects/{pid}/raid", data={
        "action": "delete",
        "raid_id": rid,
        "partial": "raid",
    })
    assert resp.status_code == 200
    with app.app_context():
        assert RaidItem.query.get(rid) is None
