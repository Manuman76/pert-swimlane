"""RAID log routes (Risks, Assumptions, Issues, Decisions)."""

from datetime import date
from flask import render_template, request, redirect, url_for

from ..db import db
from ..models import (
    Project,
    RaidItem,
    RAID_KINDS,
    RAID_KIND_LABELS,
    RAID_KIND_RISK,
    RAID_KIND_ISSUE,
    RAID_KIND_DECISION,
    RAID_CODE_PREFIX,
    RAID_STATUS_CHOICES,
    RAID_LEVEL_CHOICES,
)
from ..services.common import (
    fmt_date,
    parse_date,
    members_in_order,
    next_raid_code,
    parse_raid_kind,
    parse_raid_status,
    parse_raid_level,
)
from .context import bp


def raid_context(project):
    items = RaidItem.query.filter_by(project_id=project.id).order_by(
        RaidItem.kind, RaidItem.code
    ).all()
    by_kind = {kind: [] for kind in RAID_KINDS}
    for item in items:
        by_kind[item.kind].append(item)
    return {
        "raid_by_kind": by_kind,
        "raid_kinds": RAID_KINDS,
        "raid_kind_labels": RAID_KIND_LABELS,
        "raid_status_choices": RAID_STATUS_CHOICES,
        "raid_level_choices": RAID_LEVEL_CHOICES,
    }


def add_raid_item(project, kind, title, description=None, owner=None, status=None,
                  probability=None, impact=None, mitigation=None,
                  severity=None, due_date=None,
                  decision_date=None, decision_text=None, rationale=None):
    parsed_kind = parse_raid_kind(kind)
    if not parsed_kind:
        return None, ("RAID item type is required", 400)
    title = (title or "").strip()
    if not title:
        return None, ("Title is required", 400)

    owner_str = (owner.strip() if owner else None) if owner else None
    item = RaidItem(
        project_id=project.id,
        kind=parsed_kind,
        code=next_raid_code(project.id, parsed_kind),
        title=title,
        description=(description or "").strip() or None,
        owner=owner_str or None,
        status=parse_raid_status(status),
    )
    if parsed_kind == RAID_KIND_RISK:
        item.probability = parse_raid_level(probability)
        item.impact = parse_raid_level(impact)
        item.mitigation = (mitigation or "").strip() or None
    elif parsed_kind == RAID_KIND_ISSUE:
        item.severity = parse_raid_level(severity)
        due_raw = (due_date or "").strip() if isinstance(due_date, str) else None
        item.due_date = parse_date(due_raw) if due_raw else (due_date if isinstance(due_date, type(date.today())) else None)
    elif parsed_kind == RAID_KIND_DECISION:
        date_raw = (decision_date or "").strip() if isinstance(decision_date, str) else None
        item.decision_date = parse_date(date_raw) if date_raw else (decision_date if isinstance(decision_date, type(date.today())) else None)
        item.decision_text = (decision_text or "").strip() or None
        item.rationale = (rationale or "").strip() or None

    db.session.add(item)
    db.session.commit()
    return item, None


def update_raid_item(project, raid_id, title, description=None, owner=None, status=None,
                     probability=None, impact=None, mitigation=None,
                     severity=None, due_date=None,
                     decision_date=None, decision_text=None, rationale=None):
    item = RaidItem.query.filter_by(id=raid_id, project_id=project.id).first_or_404()
    title = (title or "").strip()
    if not title:
        return None, ("Title is required", 400)
    item.title = title
    item.description = (description or "").strip() or None
    owner_str = owner.strip() if owner else None
    item.owner = owner_str if owner_str else None
    item.status = parse_raid_status(status, item.status)

    if item.kind == RAID_KIND_RISK:
        item.probability = parse_raid_level(probability)
        item.impact = parse_raid_level(impact)
        item.mitigation = (mitigation or "").strip() or None
    elif item.kind == RAID_KIND_ISSUE:
        item.severity = parse_raid_level(severity)
        due_raw = (due_date or "").strip() if isinstance(due_date, str) else None
        item.due_date = parse_date(due_raw) if due_raw else (due_date if isinstance(due_date, type(date.today())) else None)
    elif item.kind == RAID_KIND_DECISION:
        date_raw = (decision_date or "").strip() if isinstance(decision_date, str) else None
        item.decision_date = parse_date(date_raw) if date_raw else (decision_date if isinstance(decision_date, type(date.today())) else None)
        item.decision_text = (decision_text or "").strip() or None
        item.rationale = (rationale or "").strip() or None

    db.session.commit()
    return item, None


def delete_raid_item(project, raid_id):
    item = RaidItem.query.filter_by(id=raid_id, project_id=project.id).first_or_404()
    db.session.delete(item)
    db.session.commit()
    return None, None


@bp.route("/projects/<int:id>/raid", methods=["POST"])
def manage_raid(id):
    proj = Project.query.get_or_404(id)
    action = request.form.get("action")
    raid_id = request.form.get("raid_id", type=int)
    error = None

    if action == "add":
        _, error = add_raid_item(
            proj,
            kind=request.form.get("kind"),
            title=request.form.get("title"),
            description=request.form.get("description"),
            owner=request.form.get("owner"),
            status=request.form.get("status"),
            probability=request.form.get("probability"),
            impact=request.form.get("impact"),
            mitigation=request.form.get("mitigation"),
            severity=request.form.get("severity"),
            due_date=request.form.get("due_date"),
            decision_date=request.form.get("decision_date"),
            decision_text=request.form.get("decision_text"),
            rationale=request.form.get("rationale"),
        )
    elif action == "update":
        _, error = update_raid_item(
            proj,
            raid_id=raid_id,
            title=request.form.get("title"),
            description=request.form.get("description"),
            owner=request.form.get("owner"),
            status=request.form.get("status"),
            probability=request.form.get("probability"),
            impact=request.form.get("impact"),
            mitigation=request.form.get("mitigation"),
            severity=request.form.get("severity"),
            due_date=request.form.get("due_date"),
            decision_date=request.form.get("decision_date"),
            decision_text=request.form.get("decision_text"),
            rationale=request.form.get("rationale"),
        )
    elif action == "delete":
        _, error = delete_raid_item(proj, raid_id=raid_id)
    else:
        return "Unknown RAID action", 400

    if error:
        return error

    if request.form.get("partial") == "raid":
        ctx = raid_context(proj)
        ctx.update({
            "project": proj,
            "members": members_in_order(proj.id),
            "fmt_date": fmt_date,
        })
        return render_template("partials/raid_pane.html", **ctx)
    return redirect(url_for("main.project", id=proj.id) + "#raid")
