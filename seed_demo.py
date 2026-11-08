"""Seed a complete, repeatable sample project without disturbing other projects."""

from datetime import date, datetime

from app import create_app
from app.db import db
from app.models import (
    Holiday,
    Lane,
    Project,
    RaidItem,
    Requirement,
    Task,
    TeamMember,
    TASK_KIND_MILESTONE,
)
from app.services.scheduler import recalculate_project


PROJECT_NAME = "Northstar Commerce Modernization"
PROJECT_START = date(2026, 6, 1)
PROJECT_DUE = date(2026, 12, 18)

LANE_NAMES = (
    "Product & Delivery",
    "Experience Design",
    "Platform & Data",
    "Commerce Services",
    "Quality & Security",
    "Launch & Operations",
)

MEMBERS = (
    "Avery Chen",
    "Jordan Patel",
    "Morgan Brooks",
    "Riley Thompson",
    "Casey Nguyen",
    "Taylor Reed",
    "Jamie Flores",
    "Quinn Campbell",
)

# code, name, lane, start, workdays, predecessors, owner, priority
TASKS = (
    ("001", "Program kickoff", "Product & Delivery", "2026-06-01", 0, "", "Avery Chen", "high"),
    ("002", "Confirm charter, outcomes, and funding", "Product & Delivery", "2026-06-01", 3, "001", "Avery Chen", "high"),
    ("003", "Map stakeholders and decision owners", "Product & Delivery", "2026-06-01", 4, "001", "Jordan Patel", "high"),
    ("004", "Mobilize delivery team and working agreements", "Product & Delivery", "2026-06-01", 3, "001", "Avery Chen", "medium"),
    ("005", "Interview customers and store operators", "Experience Design", "2026-06-04", 8, "002,003", "Morgan Brooks", "high"),
    ("006", "Establish analytics and conversion baseline", "Product & Delivery", "2026-06-04", 5, "002", "Jordan Patel", "high"),
    ("007", "Map current shopping and service journeys", "Experience Design", "2026-06-04", 7, "003", "Morgan Brooks", "high"),
    ("008", "Inventory catalog, customer, and order data", "Platform & Data", "2026-06-08", 5, "004", "Casey Nguyen", "high"),
    ("009", "Run privacy, legal, and regulatory workshop", "Product & Delivery", "2026-06-16", 3, "005", "Avery Chen", "high"),
    ("010", "Define MVP stories and acceptance criteria", "Product & Delivery", "2026-06-16", 5, "005,007", "Jordan Patel", "high"),
    ("011", "Approve requirements and MVP scope", "Product & Delivery", "2026-06-23", 2, "009,010", "Avery Chen", "high"),
    ("012", "Requirements and scope baseline", "Product & Delivery", "2026-06-25", 0, "011", "Avery Chen", "high"),
    ("013", "Evaluate architecture options and trade-offs", "Platform & Data", "2026-06-25", 7, "012", "Casey Nguyen", "high"),
    ("014", "Validate target personas and prototype concepts", "Experience Design", "2026-06-25", 8, "012", "Morgan Brooks", "high"),
    ("015", "Design canonical product and order data model", "Platform & Data", "2026-07-06", 6, "008,013", "Casey Nguyen", "high"),
    ("016", "Complete security threat model and controls", "Quality & Security", "2026-07-06", 5, "013", "Quinn Campbell", "high"),
    ("017", "Select payment and search integration vendors", "Commerce Services", "2026-07-06", 10, "013", "Taylor Reed", "high"),
    ("018", "Create accessible visual system and components", "Experience Design", "2026-07-06", 8, "014", "Morgan Brooks", "high"),
    ("019", "Design end-to-end service blueprint", "Experience Design", "2026-07-06", 6, "014", "Riley Thompson", "medium"),
    ("020", "Architecture and experience design approval", "Product & Delivery", "2026-07-20", 0, "015,016,017,018,019", "Avery Chen", "high"),
    ("021", "Provision development, staging, and test environments", "Platform & Data", "2026-07-21", 4, "020", "Casey Nguyen", "high"),
    ("022", "Implement CI pipeline and deployment automation", "Platform & Data", "2026-07-21", 5, "020", "Quinn Campbell", "high"),
    ("023", "Implement identity and session architecture", "Commerce Services", "2026-07-21", 5, "020", "Taylor Reed", "high"),
    ("024", "Build product catalog and merchandising service", "Commerce Services", "2026-07-28", 12, "021,022", "Taylor Reed", "high"),
    ("025", "Build search indexing and relevance service", "Commerce Services", "2026-07-28", 10, "021,022,024", "Jamie Flores", "high"),
    ("026", "Build cart, pricing, and tax calculation service", "Commerce Services", "2026-07-28", 10, "021,022", "Riley Thompson", "high"),
    ("027", "Build checkout orchestration and order service", "Commerce Services", "2026-08-11", 12, "023,026", "Riley Thompson", "high"),
    ("028", "Build customer profile and account preferences", "Commerce Services", "2026-08-11", 10, "023", "Jamie Flores", "high"),
    ("029", "Build responsive storefront and content pages", "Experience Design", "2026-08-11", 12, "018,022", "Morgan Brooks", "high"),
    ("030", "Build operations console for orders and catalog", "Commerce Services", "2026-08-25", 8, "024,028", "Taylor Reed", "medium"),
    ("031", "Implement promotion, coupon, and gift-card rules", "Commerce Services", "2026-08-25", 8, "024", "Riley Thompson", "medium"),
    ("032", "Integrate payment provider and refund workflows", "Commerce Services", "2026-08-27", 8, "017,027", "Riley Thompson", "high"),
    ("033", "Integrate warehouse inventory and availability feeds", "Platform & Data", "2026-08-27", 8, "017,024", "Casey Nguyen", "high"),
    ("034", "Instrument funnel, search, and operational events", "Platform & Data", "2026-09-02", 5, "024,025", "Jordan Patel", "medium"),
    ("035", "Apply accessibility patterns across storefront", "Experience Design", "2026-09-02", 6, "029", "Morgan Brooks", "high"),
    ("036", "Build data migration validation and rollback tools", "Platform & Data", "2026-09-02", 8, "015,021", "Casey Nguyen", "high"),
    ("037", "Publish API contracts and integration examples", "Platform & Data", "2026-09-14", 4, "024,026,028", "Quinn Campbell", "medium"),
    ("038", "Configure partner integration sandbox", "Commerce Services", "2026-09-21", 5, "032,033", "Taylor Reed", "medium"),
    ("039", "Run first production-data migration rehearsal", "Platform & Data", "2026-09-15", 3, "036", "Casey Nguyen", "high"),
    ("040", "Migrate and quality-check product content", "Platform & Data", "2026-09-15", 6, "029,036", "Morgan Brooks", "high"),
    ("041", "Execute end-to-end integration test cycle one", "Quality & Security", "2026-09-28", 8, "034,037,038", "Quinn Campbell", "high"),
    ("042", "Tune search and storefront performance", "Commerce Services", "2026-09-29", 8, "025,034", "Jamie Flores", "high"),
    ("043", "Complete privacy impact and retention review", "Quality & Security", "2026-09-29", 5, "016,028", "Quinn Campbell", "high"),
    ("044", "Run second migration rehearsal and reconcile totals", "Platform & Data", "2026-09-28", 3, "039,040", "Casey Nguyen", "high"),
    ("045", "Feature-complete build", "Product & Delivery", "2026-10-02", 0, "030,031,032,033,035,040", "Avery Chen", "high"),
    ("046", "Execute functional and regression test suite", "Quality & Security", "2026-10-05", 10, "041,045", "Quinn Campbell", "high"),
    ("047", "Verify browser, device, and responsive coverage", "Quality & Security", "2026-10-05", 5, "029,035", "Morgan Brooks", "high"),
    ("048", "Complete independent accessibility audit", "Quality & Security", "2026-10-12", 5, "035,047", "Morgan Brooks", "high"),
    ("049", "Run penetration test and remediate findings", "Quality & Security", "2026-10-12", 8, "043,045", "Quinn Campbell", "high"),
    ("050", "Run peak-load and recovery tests", "Quality & Security", "2026-10-12", 6, "042,045", "Jamie Flores", "high"),
    ("051", "Prepare UAT scenarios, accounts, and training", "Product & Delivery", "2026-10-12", 5, "045", "Jordan Patel", "medium"),
    ("052", "Conduct business-user acceptance testing", "Product & Delivery", "2026-10-19", 10, "046,047,051", "Jordan Patel", "high"),
    ("053", "Triage defects and complete release fixes", "Commerce Services", "2026-10-19", 10, "046,049,050", "Riley Thompson", "high"),
    ("054", "Write cutover, rollback, and communications plan", "Launch & Operations", "2026-10-27", 5, "044,045", "Avery Chen", "high"),
    ("055", "Create support playbooks and training materials", "Launch & Operations", "2026-10-27", 6, "030,051", "Jordan Patel", "medium"),
    ("056", "Confirm monitoring, on-call, and support readiness", "Launch & Operations", "2026-11-04", 5, "053,055", "Quinn Campbell", "high"),
    ("057", "Rehearse release cutover with business and vendors", "Launch & Operations", "2026-11-11", 3, "052,053,054,056", "Avery Chen", "high"),
    ("058", "Hold launch readiness review and close blockers", "Product & Delivery", "2026-11-16", 1, "057", "Avery Chen", "high"),
    ("059", "Approve release candidate and go/no-go evidence", "Product & Delivery", "2026-11-17", 1, "058", "Avery Chen", "high"),
    ("060", "Sign off operational runbooks and escalation paths", "Launch & Operations", "2026-11-18", 1, "059", "Quinn Campbell", "high"),
    ("061", "Execute production data migration and reconciliation", "Platform & Data", "2026-12-14", 2, "060", "Casey Nguyen", "high"),
    ("062", "Northstar commerce platform go-live", "Launch & Operations", "2026-12-18", 0, "061", "Avery Chen", "high"),
)

DONE_CODES = {f"{number:03d}" for number in range(1, 41)}
DONE_CODES.update({"045", "047", "048"})
STARTED_CODES = {"041", "042", "043", "044", "046", "049", "050", "051", "052", "053", "054", "055", "056", "057"}

REQUIREMENTS = (
    ("Responsive storefront", "The storefront must adapt cleanly to supported desktop, tablet, and mobile viewports.", "must", ("029", "035", "047")),
    ("Accessible customer journeys", "Core browse, account, cart, and checkout journeys must meet WCAG 2.2 AA expectations.", "must", ("018", "035", "048")),
    ("Customer account security", "Customers must authenticate securely and manage sessions and account preferences.", "must", ("023", "028", "049")),
    ("Product catalog management", "Authorized staff must maintain product descriptions, attributes, and availability.", "must", ("024", "030")),
    ("Relevant product search", "Customers must find products using ranked search results and useful filters.", "must", ("025", "042")),
    ("Persistent shopping cart", "Customers must review and update a cart without losing selected items.", "must", ("026", "027")),
    ("Reliable checkout", "Checkout must validate customer, delivery, tax, and payment details before order creation.", "must", ("027", "032", "046")),
    ("Secure payment processing", "Payment details must be handled through the approved provider without storing card data.", "must", ("017", "032", "043", "049")),
    ("Order confirmation", "Customers must receive an accurate confirmation after a successful order.", "must", ("027", "032", "046")),
    ("Promotion redemption", "Eligible customers must redeem valid promotions and gift cards at checkout.", "should", ("031", "046")),
    ("Inventory accuracy", "Displayed availability must reflect warehouse updates within the agreed freshness window.", "must", ("033", "038")),
    ("Operations order console", "Support staff must locate orders and view fulfillment and payment status.", "should", ("030", "055")),
    ("Content migration", "Approved product and editorial content must be migrated and reconciled before launch.", "must", ("036", "039", "040", "044")),
    ("Privacy and data retention", "Personal data collection, access, retention, and deletion must follow approved policy.", "must", ("009", "016", "043")),
    ("Analytics funnel tracking", "Product, search, cart, and checkout events must support conversion reporting.", "should", ("006", "034")),
    ("Search performance", "Search responses must meet the approved latency target at forecast peak traffic.", "must", ("025", "042", "050")),
    ("Peak traffic capacity", "The platform must sustain forecast launch demand without unacceptable degradation.", "must", ("042", "050")),
    ("Browser compatibility", "The core customer journey must work on the agreed browser and device matrix.", "must", ("047", "052")),
    ("API integration contracts", "Internal and partner APIs must have documented schemas and stable error behavior.", "should", ("037", "038", "041")),
    ("Warehouse integration", "Order and inventory updates must synchronize with the warehouse system.", "must", ("033", "038", "041")),
    ("Refund handling", "Authorized staff must initiate refunds through the approved payment workflow.", "should", ("030", "032", "046")),
    ("Order data integrity", "Order totals and line items must remain consistent across services and reports.", "must", ("015", "027", "041", "044")),
    ("Deployment repeatability", "Builds and deployments must be automated and repeatable across environments.", "must", ("021", "022", "057")),
    ("Operational observability", "Critical services must emit actionable health, error, and business metrics.", "must", ("034", "050", "056")),
    ("Recovery and rollback", "The team must be able to restore service and roll back a release using a tested plan.", "must", ("036", "050", "054", "057")),
    ("Business acceptance", "Business owners must verify agreed acceptance scenarios before release approval.", "must", ("010", "051", "052", "058")),
    ("Support readiness", "Support teams must have training, escalation paths, and customer response guidance.", "should", ("055", "056", "060")),
    ("Release communications", "Customers and internal teams must receive clear, approved launch communications.", "should", ("003", "054", "057")),
    ("Launch evidence and approval", "Go-live must be supported by traceable test, security, migration, and readiness evidence.", "must", ("048", "049", "052", "058", "059")),
    ("Future loyalty capability", "The architecture should leave room for a later loyalty program without expanding MVP scope.", "could", ("013", "015")),
)

# kind, title, owner, status, description, and kind-specific values
RAID_ITEMS = (
    ("risk", "Payment-provider certification may slip", "Taylor Reed", "open", "External certification timing could compress checkout validation.", {"probability": "medium", "impact": "high", "mitigation": "Reserve vendor test windows early; keep sandbox credentials and a fallback test plan ready."}),
    ("risk", "Legacy catalog data quality is uneven", "Casey Nguyen", "open", "Incomplete attributes could reduce search quality or delay migration reconciliation.", {"probability": "high", "impact": "medium", "mitigation": "Profile source data, assign business owners to exceptions, and gate each rehearsal on reconciliation."}),
    ("risk", "Peak demand exceeds forecast", "Jamie Flores", "open", "A launch promotion could create traffic above the capacity model.", {"probability": "medium", "impact": "high", "mitigation": "Load-test a 2x forecast, confirm autoscaling limits, and prepare traffic controls."}),
    ("risk", "UAT availability conflicts with business operations", "Jordan Patel", "closed", "Store and support representatives have limited time for acceptance testing.", {"probability": "medium", "impact": "medium", "mitigation": "Book named testers and scenario sessions during discovery; use a clear defect triage window."}),
    ("assumption", "Existing warehouse API remains available", "Casey Nguyen", "open", "The current warehouse service and its test environment will remain supported through launch.", {}),
    ("assumption", "Business owners can approve MVP scope", "Avery Chen", "closed", "Product and operations decision owners can provide timely scope and acceptance approvals.", {}),
    ("assumption", "No material tax-policy change before launch", "Riley Thompson", "open", "Tax calculation rules remain stable during implementation and rollout.", {}),
    ("issue", "Staging payment credentials expired", "Taylor Reed", "closed", "The vendor rotated sandbox credentials and blocked checkout integration tests.", {"severity": "medium", "due_date": "2026-09-25"}),
    ("issue", "Duplicate SKUs found in legacy export", "Casey Nguyen", "open", "The first catalog profile identified duplicate identifiers requiring a business disposition.", {"severity": "high", "due_date": "2026-11-20"}),
    ("issue", "Keyboard focus order inconsistent in cart", "Morgan Brooks", "open", "An accessibility review found a focus-order defect in the cart summary.", {"severity": "medium", "due_date": "2026-11-13"}),
    ("issue", "Warehouse test feed intermittently delayed", "Casey Nguyen", "open", "Partner sandbox updates arrive late during high-volume test runs.", {"severity": "medium", "due_date": "2026-11-18"}),
    ("issue", "UAT tester accounts missing permissions", "Jordan Patel", "closed", "Two business testers could not access the new operations console on day one.", {"severity": "low", "due_date": "2026-10-23"}),
    ("decision", "Adopt managed payment provider", "Avery Chen", "closed", "Use the selected managed provider for card authorization, capture, and refunds.", {"decision_date": "2026-07-06", "decision_text": "Proceed with the managed payment provider; do not store payment-card data in Northstar.", "rationale": "Reduces PCI scope and delivery effort while meeting the required regional payment coverage."}),
    ("decision", "Keep loyalty program outside MVP", "Avery Chen", "closed", "Defer points, rewards, and loyalty account migration to a later phase.", {"decision_date": "2026-06-23", "decision_text": "Loyalty capabilities are explicitly out of the December MVP.", "rationale": "Protects the launch date and keeps the first release focused on reliable core commerce."}),
    ("decision", "Use a five-day delivery calendar", "Avery Chen", "closed", "Plan delivery using Monday-Friday workdays and observe the listed team holidays.", {"decision_date": "2026-06-01", "decision_text": "Adopt a Monday-Friday project calendar.", "rationale": "Matches the participating teams' standard working pattern and makes the critical path legible."}),
    ("decision", "Use staged catalog migration rehearsals", "Casey Nguyen", "closed", "Require two reconciled rehearsals before final production migration.", {"decision_date": "2026-08-14", "decision_text": "Run two timed rehearsals with source-to-target count and value reconciliation.", "rationale": "Provides evidence for cutover duration, data quality, and rollback readiness."}),
    ("decision", "Launch with responsive web, not native apps", "Avery Chen", "closed", "The December release will deliver a responsive web storefront only.", {"decision_date": "2026-06-23", "decision_text": "Do not include native mobile applications in MVP.", "rationale": "A responsive web experience reaches all target devices with one launch-critical implementation."}),
)

HOLIDAYS = (
    (date(2026, 6, 19), "Juneteenth"),
    (date(2026, 7, 3), "Independence Day observed"),
    (date(2026, 9, 7), "Labor Day"),
    (date(2026, 11, 26), "Thanksgiving"),
    (date(2026, 11, 27), "Day after Thanksgiving"),
)


def seed(app=None):
    app = app or create_app()
    with app.app_context():
        project = Project.query.filter_by(name=PROJECT_NAME).first()
        if project is not None:
            db.session.delete(project)
            db.session.flush()

        project = Project(
            name=PROJECT_NAME,
            description=(
                "<p>Modernize Northstar's customer commerce experience and "
                "operations platform. The December release delivers an accessible "
                "responsive storefront, reliable checkout, integrated catalog and "
                "inventory services, migrated content, and launch-ready operations.</p>"
                "<p><strong>Planned start:</strong> 01 Jun 2026 &nbsp; "
                "<strong>Go-live deadline:</strong> 18 Dec 2026</p>"
            ),
        )
        db.session.add(project)
        db.session.flush()

        lanes = {}
        for sort_order, name in enumerate(LANE_NAMES, 1):
            lane = Lane(project_id=project.id, name=name, sort_order=sort_order)
            db.session.add(lane)
            lanes[name] = lane
        for name in MEMBERS:
            db.session.add(TeamMember(project_id=project.id, name=name))
        for holiday_date, name in HOLIDAYS:
            db.session.add(Holiday(project_id=project.id, date=holiday_date, name=name))
        db.session.flush()

        tasks_by_code = {}
        for code, name, lane_name, start, duration, predecessors, owner, priority in TASKS:
            done = code in DONE_CODES
            started = code in STARTED_CODES
            task = Task(
                project_id=project.id,
                code=code,
                kind=TASK_KIND_MILESTONE if duration == 0 else "task",
                lane_id=lanes[lane_name].id,
                name=name,
                start_date=date.fromisoformat(start),
                duration_days=duration,
                predecessors=predecessors or None,
                responsible=owner,
                priority=priority,
                description=(
                    f"Deliver {name.lower()} for the Northstar commerce modernization. "
                    f"Coordinate with the {lane_name.lower()} workstream and record "
                    "evidence, open questions, and acceptance results before handoff."
                ),
                notes=(
                    "Completed and accepted." if done else
                    "In progress; update the task detail with evidence and blockers." if started else
                    "Planned; confirm readiness and dependencies before work begins."
                ),
                started=started,
                done=done,
                progress_percent=100 if done else (60 if started else 0),
            )
            if code in {"022", "036", "054", "057"}:
                task.links = "https://docs.example.com/northstar/" + {
                    "022": "delivery/ci-cd",
                    "036": "data/migration-playbook",
                    "054": "launch/cutover-plan",
                    "057": "launch/rehearsal-evidence",
                }[code]
            db.session.add(task)
            tasks_by_code[code] = task
        db.session.flush()

        for title, description, moscow, linked_codes in REQUIREMENTS:
            requirement = Requirement(
                project_id=project.id,
                code=f"BR-{Requirement.query.filter_by(project_id=project.id).count() + 1:03d}",
                title=title,
                description=description,
                moscow=moscow,
                status="approved",
            )
            requirement.tasks = [tasks_by_code[code] for code in linked_codes]
            db.session.add(requirement)

        raid_sequences = {}
        for kind, title, owner, status, description, details in RAID_ITEMS:
            raid_sequences[kind] = raid_sequences.get(kind, 0) + 1
            details = details.copy()
            for date_field in ("due_date", "decision_date"):
                if date_field in details:
                    details[date_field] = date.fromisoformat(details[date_field])
            item = RaidItem(
                project_id=project.id,
                kind=kind,
                code=f"{kind[0].upper()}-{raid_sequences[kind]:03d}",
                title=title,
                description=description,
                owner=owner,
                status=status,
                **details,
            )
            db.session.add(item)

        db.session.flush()
        recalculate_project(project.id)
        for task in tasks_by_code.values():
            task.baseline_start_date = task.start_date
            task.baseline_end_date = task.end_date
            task.baseline_duration_days = task.duration_days
        project.baseline_set_at = datetime.utcnow()
        db.session.commit()

        print(
            f"Seeded '{project.name}': {len(tasks_by_code)} tasks, "
            f"{len(REQUIREMENTS)} requirements, {len(RAID_ITEMS)} RAID items, "
            f"and {len(LANE_NAMES)} swimlanes."
        )


if __name__ == "__main__":
    seed()
