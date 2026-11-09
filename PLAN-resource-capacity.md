# Plan: Resource Capacity

Add resource (people) capacity planning to PERT Swimlane: each team member gets an
availability profile, each task carries how much of a person it consumes, and the app
computes **supply vs demand per member per week** so over-allocation is visible before
the schedule slips.

**Status:** P1 is built — schema, `app/capacity.py`, 63 tests in `tests/test_capacity.py`
(187 passing overall, unchanged failure count). P2–P5 are not started. Where the build
settled a question the prose left open, it is marked **as built**.

Design constraints inherited from the codebase:

- **Derived, never stored.** Like `end_date`, `variance_days` and requirement coverage,
  load is *computed* from the grid. No cached load columns.
- **Working days are the unit.** The whole app already counts working days
  (`app/workdays.py`, `duration_days`, `count_workdays`). Capacity therefore uses
  **person-days**, not hours — one unit, no conversion table, and it stays correct on a
  4-day or 6-day project calendar.
- **HTMX + server-rendered HTML only.** No JS framework, no chart library, no build step,
  nothing fetched off the machine (that is what makes the PyInstaller desktop build work).
- **Local-first, single user.** No accounts, so no "who owns this resource" model.

---

## 1. Decisions (recommendations in **bold**)

| # | Question | Options | Recommendation |
|---|---|---|---|
| 1 | Unit of capacity | **person-days/week** · hours/week · FTE | **person-days/week**, derived from the project calendar; show `%` in the UI |
| 2 | Assignment cardinality | **one `responsible` per task** · many assignees per task | **keep one owner** for v1; `allocation_percent` on the task. Multi-assignee is a later phase (§10) behind the same compute core |
| 3 | Time bucket | **ISO week** · day · month | **ISO week** — matches delivery cadence, keeps a 6-month project at ~26 columns |
| 4 | Over-allocation behaviour | **warn only** · block the edit | **warn only.** The scheduler owns dates; capacity is advisory. Blocking would make the grid unsavable during the exact crunch it is meant to describe |
| 5 | Absence model | **per-member date ranges** · reuse project `Holiday` | **new `member_absences` table**; project holidays stay org-wide |
| 6 | Completed work | count full effort · **count remaining effort** · exclude | **remaining effort** (`duration × alloc × (100 − progress)/100`) for windows touching today; `done` tasks contribute 0 |

Say "go" with these defaults, or name the ones you want changed — only #1 and #2 touch
the schema, so the rest can shift after P1.

**Unit rule: person-days, not hours.** One unit through the whole app — `duration_days`
is elapsed working days, capacity is person-days of effort. Hours would force a
days-per-week conversion table onto a codebase that already counts days and already
handles 4-day and 6-day calendars. If we ever need hours (hourly billing, a client rate
card in hours), convert *in the report template*, never in the model.

**Effort ≠ duration.** The schedule speaks elapsed time, capacity speaks effort; the
bridge is `allocation_percent`. A 5-day task at 50% occupies 5 elapsed working days and
2.5 person-days of effort — the schedule and the capacity grid must agree, or the tab
contradicts the diagram. Two people at 50% on a 5-day task is 5 person-days of work
spread over one calendar week.

**Calendar consistency.** Capacity uses the *project's* `WorkdayCalendar` — its
working-day pattern and its holidays — so a Friday hand-off day does not become a
working day for someone, and a project holiday lowers supply and demand together.

**Local storage only.** `MemberAbsence` is a normal model, which keeps
`desktop.py::_safe_export_payload` working unchanged.

---

## 2. Data model

`app/models.py` — extend `TeamMember`, add two models:

```python
class TeamMember(db.Model):
    # existing: id, project_id, name, created_at
    role            = db.Column(db.Text, nullable=True)            # "QA", "Dev lead"
    capacity_percent = db.Column(db.Integer, nullable=False, default=100)  # 100 = 1 person-day per working day
    cost_per_day    = db.Column(db.Numeric(8, 2), nullable=True)   # optional, unused in v1 charts
    absences = db.relationship("MemberAbsence", cascade="all, delete-orphan",
                               order_by="MemberAbsence.start_date")

    @property
    def capacity_fraction(self): ...    # clamped 0–400, 0–4 when unset

    @property
    def display_label(self): ...        # "Ada — Tech lead"
```

```python
class MemberAbsence(db.Model):          # vacation, sickness, other projects
    __tablename__ = "member_absences"
    id, project_id, member_id(FK -> team_members, ondelete cascade)
    start_date = db.Date, nullable=False       # inclusive
    end_date   = db.Date, nullable=False       # exclusive, like Task.end_date
    reason     = db.Text, nullable=True
    __table_args__ = (db.UniqueConstraint("member_id", "start_date", "end_date",
                                          name="uq_absence_range"),)
    # + a `label` property: "6 - 7 Jul 2026", or "8 Jun 2026" for a single day
```

`Task` gets one column:

```python
allocation_percent = db.Column(db.Integer, nullable=True)   # NULL = 100; milestones ignore it
```

Notes:

- `responsible` stays a **name string** (it is today, and `rename_team_member` rewrites
  it). Capacity therefore groups by name; §9 covers the ghost-name case. Do not
  introduce a `member_id` FK in this change — that migration is its own PR.
- **Nothing is validated on write**: `capacity_percent` and `allocation_percent` are
  clamped where they are read (`capacity_fraction`, `_allocation_fraction`), so a bad
  number in an old row degrades rather than raising, and the P2 form can show its own
  range error without the model fighting the UI.
- Capacity is **per project**, like members, lanes, workdays and holidays. Portfolio-wide
  resource pooling across projects is out of scope (v1 has one SQLite file per install and
  members are already per project).

### Migration

`_migrate_schema()` in `app/__init__.py` (hand-rolled `ALTER TABLE`, no Alembic — the
README is explicit about this):

```python
member_columns = {r[1] for r in db.session.execute(text("PRAGMA table_info(team_members)"))}
for col, ddl in {"role": "TEXT", "capacity_percent": "INTEGER NOT NULL DEFAULT 100",
                 "cost_per_day": "NUMERIC(8,2)"}.items():
    if col not in member_columns:
        db.session.execute(text(f"ALTER TABLE team_members ADD COLUMN {col} {ddl}"))

task_cols = {r[1] for r in db.session.execute(text("PRAGMA table_info(tasks)"))}
if "allocation_percent" not in task_cols:
    db.session.execute(text("ALTER TABLE tasks ADD COLUMN allocation_percent INTEGER"))
```

`member_absences` needs no entry — `db.create_all()` creates missing *tables*; only
columns are a problem. Add a test that boots an old-schema DB and upgrades it (§8).

---

## 3. Compute core: `app/capacity.py`

A pure module, same shape as `requirement_trace.py` / `task_tree.py` /
`project_overview.py`: no flask imports, no DB writes, easy to unit test.

```python
BUCKET_WEEKS = "week"          # room for BUCKET_MONTHS later

def build_capacity(project=None, tasks=None, members=None, *, weeks=None,
                   window_start=None, window_end=None,
                   min_weeks=CAPACITY_MIN_WEEKS, max_weeks=CAPACITY_MAX_WEEKS,
                   remaining_effort=True, today=None) -> SimpleNamespace: ...

def find_member_row(caps, name) -> row or None          # name matching is forgiving
def worst_week(caps, name, start=None, end=None) -> week cell or None   # §5.3 grid dots
def capacity_state(demand, supply) -> "none"|"idle"|"normal"|"tight"|"over"
def capacity_percent(demand, supply) -> int or None     # None when there is no supply
fmt_capacity_days(v) / fmt_capacity_percent(v)          # "2.5d" / "110%" / "—"
week_start / week_ends / week_label / week_full_label / week_head_label / month_label
```

That is the whole public surface, and every name is tested — P2–P5 add what they need
rather than growing helpers on spec. The task-window lookup §5.3 wants is
`worst_week(caps, task.responsible, task.start_date, task.end_date)`; no per-task helper
is needed until multi-assignee work exists.

Algorithm (single pass per task, O(tasks × weeks); 62 tasks × 26 weeks × 8 members is
trivially fast — the diagram's A\* router dwarfs this):

1. **Window.** Default `min(task.start_date)` → `max(task.end_date)`, rounded out to whole
   ISO weeks (Mon–Sun), at least `CAPACITY_MIN_WEEKS = 8` and capped at
   `CAPACITY_MAX_WEEKS = 26` with a `window_truncated` flag the template turns into a
   "showing 26 of 31 weeks" note. Padding is skipped when the caller bounded the end —
   padding is there so a short plan shows a horizon, and that argument does not apply to
   a window someone asked to be three weeks.
2. **Supply** per member per week:
   `working_days_in_week(calendar, week)` — reuse `WorkdayCalendar.is_workday`, so project
   working days *and* holidays are already honoured —
   × `capacity_percent / 100`, minus overlapping `MemberAbsence` days clipped to that week
   and to working days (absence on a Saturday consumes nothing). A day set keeps two
   overlapping ranges from eating the same Tuesday twice.
   **As built:** today does not pro-rate supply; it only paints the past/now/future
   columns. Pro-rating the current week needs a rule for tasks that start late or early,
   and it would make every capacity test date-sensitive for a number the grid already
   shows as "this week".
3. **Demand** per member per week: for each non-milestone task with a `responsible`,
   clip `[start_date, end_date)` to the week, count the overlap with
   `count_workdays(overlap_start, overlap_end, calendar=project.calendar)`, multiply by
   `allocation_percent or 100`, and (when `remaining_effort`) by
   `(100 - task.display_progress) / 100`. Milestones contribute 0 — zero duration.
   The exclusive end matters: a task ending Mon 15 Jun is not worked in W25, and the
   weekly buckets are then an exact partition of the task's working days.
4. **Unassigned demand** is accumulated in a synthetic row (`member=None`) so the tab can
   say "9 person-days are unowned this week" instead of hiding it. **As built** it is
   measured against one hypothetical full-time person per week, so its percentage reads
   as "how much of a week does nobody own yet"; it is excluded from `totals` and
   `members_over`, which describe people.
5. **Ghost assignees** — names in `Task.responsible` with no `team_member` row — get a row
   of their own (`kind="ghost"`, `supply=0`) because their work is real even though the
   settings tab has never heard of them.
6. Per member also compute: `total_demand`, `total_supply`, `utilization_percent`,
   `peak_percent` / `peak_week_index`, `weeks_over` (any week `> SUPPLY`), `weeks_idle`,
   `first_over_date`, `total_over_days`, and a per-week `state` in
   `{none, idle, normal, tight, over}` using `CAPACITY_THRESHOLD_IDLE = 0.5`,
   `TIGHT = 0.9`, `OVER = 1.0` module constants.
7. Return `SimpleNamespace(weeks=[…columns…], rows=[…], unassigned_row, ghosts, totals,
   members_over, members_idle, weeks_over, first_over_date, peak_percent,
   peak_week_index, window_start/end/weeks, window_truncated, span_start, span_end,
   has_capacity_data=bool(members), has_work, mode, today, calendar)` — mirroring the
   `build_project_overview` return style the templates already expect. `span_start` /
   `span_end` stay the unrounded project dates so a header can talk about the plan while
   the grid talks about the chart.

Rounding: keep 4 decimals internally (weekly buckets sum to exact day counts), format for
display with `fmt_capacity_days` (`"1.5d"`, `"2d"`, `"—"`) and `fmt_capacity_percent`
(`"110%"`, `"88%"`, `"—"`). Cells show `%` (utilization) and `x.xd / y.yd` in the tooltip.

---

## 4. Routes (`app/routes/capacity.py`, new)

Registered by re-export in `app/routes/__init__.py` like the other route modules
(it is a compat shim — new code imports from the specific module).

| Method | Path | Description |
|---|---|---|
| GET | `/projects/<id>/capacity` | Capacity tab partial (`?start=YYYY-MM-DD&weeks=13&mode=remaining\|full`) |
| GET | `/projects/<id>/capacity/export.csv` | Member × week CSV (reuses the Jira CSV response pattern) |
| POST | `/projects/<id>/absences` | `add` · `delete` (mirrors `manage_holidays`) |
| POST | `/projects/<id>/members` *(extend)* | new `capacity` action: role, capacity %, cost/day; `add` accepts an optional initial capacity |

Extend `app/routes/tasks.py` so both `update_task` and `update_task_detail` accept
`allocation_percent` (empty → NULL, integer 0–100, milestones rejected, anything else →
existing **422 + re-rendered row/panel with draft values preserved** path — reuse the
`panel_errors` / row error-badge machinery, do not invent a new error channel).

Invalid absence ranges (missing/`end_date <= start_date`/unknown member) return plain
`400`s from the simple form endpoint, matching the lanes/holidays convention rather than
the 422 draft flow.

Trigger wiring: add `refreshCapacity` to the existing fan-out so the tab follows
grid/calendar edits exactly like diagram and stats do.

- `app/routes/context.py::task_table_swap_response` → `"refreshStats, refreshDiagram, refreshRequirements, refreshCapacity"`
- `app/routes/settings.py::update_project_workdays` and `manage_holidays` →
  `"refreshStats, refreshDiagram, refreshGrid, refreshCapacity"` (working days and
  holidays change *supply*; absences change it too)
- `manage_members` / new `capacity` action → add `refreshCapacity`
- `app/templates/project.html` capacity pane: `hx-trigger="load, refreshCapacity from:body"`
- deep link: `tabForHash['#capacity'] = '#capacity-tab'` (the project.html tab-hash map)

Context plumbing: add a `capacity_context(project, tasks=None)` helper next to
`overview_context` in `app/routes/context.py`, spread into `main.project` and the overview
partial so the tab renders on first paint.

---

## 5. UI

### 5.1 Settings — capacity per resource

`app/templates/partials/settings_members.html`: extend each member row from
`name + delete` to a compact 3-field form (`name`, `role`, `capacity %`) inside the
existing `hx-target="#settings-members-list"` form, plus an inline **Availability**
section below the list: absence range + reason, added/deleted via the HTMX pattern in
`settings_holidays.html` (which already posts `partial=settings` and returns the fragment).
Add `#settings-absence-list` as the swap target.

Keep the "Team members" card header subtitle honest: *"Available for task assignment ·
capacity planning"*.

### 5.2 New **Capacity** tab (after RAID, before Settings)

New `app/templates/partials/capacity.html`, included in a `#capacity-pane` with a nav tab
button styled exactly like the existing six:

- Toolbar: window (`From`, `Weeks` 8/13/26/whole project), mode toggle
  (Remaining effort / Full plan), and *Export CSV*.
- The table: sticky first column (name + role + capacity %), one column per ISO week
  (`W23`, month grouping in the header row — same horizontal-scroll idiom as the task
  grid), cell text `87%`, `title` tooltip `Alice · 3.0d of 4.0d · 0.5d absence · tasks 004, 007`.
- Cell classes: `cap-none / cap-idle / cap-normal / cap-tight / cap-over` — a muted
  sequential ramp plus a red fill for over, never colour alone: over cells also carry a
  `▲` glyph and the `%` text (the project already avoids colour-only meaning, cf. status
  icons in `STATUS_STYLES`).
- Row footer per member: `Allocated · Available · Util`, and a red badge
  `2 weeks over` with `first_over_date` in the tooltip.
- Last row: **Unassigned**, and a **Team total** row.
- Below the grid: one CSS bar per member (`width: min(util,150)%`, 100% tick mark) — a
  "load profile" the same way the Overview KPI bars are done today, no charting lib.
- Empty state when a project has members but no capacity signal: *"Set capacity in
  Settings → Team members to plan workload."*

Scope CSS under a `.capacity-` prefix in `app/static/css/style.css` in a clearly marked
block, per the README's note about `.task-card` scoping leaking into Bootstrap panels.

### 5.3 Task grid + detail modal

- Detail modal (`task_detail_panel.html`): next to **Responsible**, add
  **Allocation %** (number input, 5-step, placeholder `100`, help text
  *"Share of this person's day while the task runs"*). Support the `draft.get('allocation_percent')`
  pattern the other fields already use for 422 re-renders.
- Grid: **do not** add a column — the grid is deliberately one compact line per task.
  Instead, put the allocation in the `Responsible` select's `title`, and mark it: when
  `capacity` shows that member over capacity during the task's own window, append a
  `⚠` + tooltip (`"Alice is at 140% in the week of 12 Oct"`). Data comes from
  `task_table_render_kwargs`, which gains `capacity_map` (computed once per render,
  task-window → worst utilization for that assignee).
- Diagram cards: leave untouched in v1 (the A\* router and fixed 168×108 card grid make
  an extra chip a layout change, not a style change).

### 5.4 Overview + portfolio

- `build_project_overview(...)` gains an optional `capacity=None` argument and
  `members_over`, `capacity_peak_percent`, `capacity_window_weeks` fields;
  `overview_context` passes `build_capacity(...)` results.
- Overview tab: a KPI card **"Resources"**, `3 of 8 over capacity · peak 165%`, linking to
  `#capacity`.
- Health: over-allocated members add `at_risk` (`"2 people over capacity"`); sustained
  crunch — `weeks_over >= 2` for the same person or peak `>= 200%` — escalates to
  `critical`. Thresholds as named constants next to the existing `> 5` working-day ones,
  so the rule table in the README stays the source of truth.
- Portfolio dashboard (`get_project_stats` + `index.html`): per-project card shows a small
  red `N over` badge; add an `over_allocated_count` KPI next to *Attention*. Keep it cheap
  — `index()` already builds an overview per project, so reuse the same call and skip
  recomputing the heatmap window for the cards (use the "whole project, coarse" mode).

---

## 6. Exports and seed

- `app/jira_export.py`: add `Allocation %` and `Capacity Role` columns (the file already
  carries extra PERT columns for custom-field mapping; append rather than reorder so
  existing import templates keep mapping).
- `seed_demo.py`: give the 8 demo members varied capacities (one at 60%, one at 150%
  across two overlapping tasks), 3 absences, and allocations of 25/50/100 across the 62
  tasks so the tab demonstrates idle, normal and over in one screen. Respect the script's
  "replace the demo project only" contract — capacity rows hang off members/tasks of that
  project, so they are replaced with it. Update `tests/test_seed_demo.py` counts.

---

## 7. Phased delivery (each phase ships and tests green on its own)

| Phase | Content | Files | ~Size |
|---|---|---|---|
| **P1** ✅ | Schema + migration + `app/capacity.py` core + unit tests (no UI) | `models.py`, `__init__.py`, `capacity.py`, `tests/test_capacity.py` | M |
| **P2** | Settings UI: role/capacity %/cost, absences CRUD, `manage_members` `capacity` action | `routes/settings.py`, `routes/capacity.py`, `partials/settings_members.html`, `partials/settings_absences.html` | M |
| **P3** | Capacity tab: heatmap, load bars, window controls, CSV export, `refreshCapacity` fan-out, `#capacity` deep link | `routes/capacity.py`, `partials/capacity.html`, `project.html`, `style.css` | L |
| **P4** | Task-level allocation: detail-modal field, grid `⚠` hint, validation, 422 draft round-trip | `routes/tasks.py`, `partials/task_detail_panel.html`, `partials/task_row.html` | M |
| **P5** | Overview + portfolio KPIs and health rules, Jira columns, seed data, README | `project_overview.py`, `index.html`, `project_overview.html`, `jira_export.py`, `seed_demo.py`, `README.md` | M |

**P1 delivered** (63 tests in `tests/test_capacity.py`, suite green apart from the
pre-existing date-sensitive `test_overdue_project_health_status`):

- `TeamMember.role/capacity_percent/cost_per_day/capacity_fraction/absences`,
  `MemberAbsence`, `Task.allocation_percent`, both `_migrate_schema()` additions.
- `app/capacity.py` end to end, including `worst_week` — the §5.3 grid hint is one call
  to it, so P4 has nothing left to compute.
- Mutation-checked: deliberately breaking absence subtraction, the allocation weight,
  remaining effort, the exclusive-end week split, the calendar plumbing, the ghost row and
  both window bounds each fails at least one test — the suite is not decorative.

## 8. Tests

`tests/test_capacity.py` — **written and passing** (P1). Pure module tests use
`SimpleNamespace` fakes plus the real `WorkdayCalendar`, and every fixture pins
`window_start` so nothing depends on today's date; the DB-backed half uses the `app` /
`seeded` fixtures and covers defaults, the allocation column, the project calendar,
holidays and the old-schema migration.

Still owed by later phases:

`tests/test_capacity_routes.py` (P2, P3):

- `GET /projects/<id>/capacity` renders 200 and contains the member names + week headers.
- `POST /projects/<id>/absences` `add`/`delete` round-trip, HTMX partial +
  `HX-Trigger-After-Swap` includes `refreshCapacity`.
- `manage_members` `capacity` action persists and clamps `capacity_percent` to 0–400.
- Task detail save with `allocation_percent=150` → **422** and the panel contains the error;
  valid `40` persists.
- Milestone with `allocation_percent` is ignored/rejected.
- Absence CRUD: add, add-reversed-range (422), delete, cascade with the member and with
  the project.

Regression touch-ups: `tests/test_seed_demo.py` (new counts), `tests/test_project_overview.py`
(health rules now include capacity — pass `capacity=None` explicitly in existing tests to
keep them hermetic). **Do not inherit the known date-sensitive failure**: use fixtures
anchored to `date.today()` with explicit working-day offsets, and don't rely on "10 calendar
days == 6 working days".

## 9. Risks and edge cases

- **`responsible` is free text, not a FK.** `rename_team_member` rewrites task names and
  `delete_team_member` clears them, so capacity must recompute from current names; ghost
  names (`"(not in team)"` in the grid today) get reported but never counted. A follow-up
  PR should add `tasks.member_id` and keep `responsible` as a derived display string.
- **Multiple people on one task** is the most-requested follow-on. Keep
  `capacity.py`'s demand accumulator keyed by member name so a `task_assignments` table can
  feed the same accumulator with no rewrite.
- **Capacity must never change dates.** Keep the compute read-only; do not call
  `recalculate_project` from capacity code, and do not let over-allocation silently push
  start dates. Level-loading (auto-defer over-allocated tasks) is explicitly **out of
  scope** — it fights the "dates only move forward" rule in `recalculate_project`.
- **Effort ≠ duration.** `duration_days` is elapsed working days, not person-days: two
  people at 50% for 5 days = 5 person-days over 1 week. UI copy must not blur the two
  words (the Tasks header already uses *Effort* for a working-day total — reuse that term
  consistently in tooltips).
- **Performance.** One computation per render for the tab, one per project for the cards.
  If `index()` slows down, cache per request with `flask.g` rather than the DB.
- **Print/PNG.** The capacity table is wide; `dom-to-image-more` export is Diagram-only
  today. If capacity needs a PNG later, reuse the diagram export path; CSV covers the
  reporting need now.

## 10. Out of scope (explicitly, so it stays a review)

Portfolio-wide resource pool shared across projects · cost/finance reporting beyond storing
`cost_per_day` · automatic resource levelling · skills-based matching and auto-assignment ·
multi-assignee tasks · hourly timesheets or actuals · anything requiring a JS charting
library or a network request.

## 11. Docs to update

`README.md`: feature bullet under *What's in it*; project-layout tree (`capacity.py`,
`routes/capacity.py`, `partials/capacity.html`, new test modules); *Domain model* (new
columns + `member_absences`); *Routes* table (4 rows); *Tabs* list
(`#overview … #capacity, #settings`); the health-status table (capacity rule + reason
line); a new **"Resource capacity"** section in the style of *"Calendar: working days and
holidays"* spelling out the person-day unit, the week bucket, remaining-effort mode and
the thresholds; and the `124 passed, 1 failed` status line plus the seed description once
the numbers move.

---

## 12. Questions, settled and open

**Settled while building P1** — say so now if you disagree, each is cheap to change today
and expensive in P3:

1. **"100% done" in the capacity model means "not started".** A task at 100% progress
   contributes 0 remaining effort even if its dates are in the future. Today the progress
   bar treats 100% as "no percent recorded", which is fine for a bar and would be
   understated here; capacity reads `display_progress` with the same 100/50/0 fallback as
   the bar, so a recorded 100 is honoured and an empty field is 0. The risk is small
   either way while `progress_percent` is optional (open question 3 in `prompt.md`), and
   both readings agree once progress is required.
2. **No pro-rating of the current week.** `today` tints columns; it does not shrink
   demand or supply. Pro-rating needs a rule for tasks that start late or early, and it
   would make every capacity number in the app depend on the date it is read — including
   in tests.
3. **`week_start` is not configurable.** Weeks are Monday-anchored like every other
   calendar decision in the app (`week_start()` / `week_ends()` in `capacity.py`); an ISO
   setting for week starts would be read there. The grid's `column_order` setting does not
   touch it, since capacity has no lane ordering to reuse.

**Open — decide when P2 is picked up:**

4. **What clamps what against the calendar.** A Mon–Wed project member has 3 available
   days, not 5 × 0.6, because supply already counts the project calendar. So should
   Settings refuse "Friday" for a member with 60% capacity, or let it stand as a
   statement about a *different* project? Today `_migrate_schema` and the compute core
   accept it silently (P1 treats the calendar as the ruler and the percentage as the
   wish); the P2 form is where a validation message could live.
5. **Whether the unowned row belongs in `members_over`.** P1 excludes it (it counts
   people); the tab can show it as a separate "unowned work" line. If the KPI card should
   count unowned work as a problem, that is a one-line change in `build_capacity`.

---

**Status:** P1 built and green; P2–P5 unwritten.
**Plan taken from:** this conversation, 10 Oct 2026, against `master` at `a406b00`. The
shape (person-days, ISO weeks, warn-only, per-project, one owner) came from the two
rounds of questions that opened the work.
