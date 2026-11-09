# Plan: Resource Capacity

Add resource (people) capacity planning to PERT Swimlane: each team member gets an
availability profile, each task carries how much of a person it consumes, and the app
computes **supply vs demand per member per week** so over-allocation is visible before
the schedule slips.

**Status:** P1–P4 are built — schema, `app/capacity.py`, the compute tests, the
Settings side (role / capacity % / cost, absence CRUD, an availability preview), the
Capacity tab with its CSV export, and the task's own share of its owner (detail-modal
**Allocation**, the grid's `⚠` week warning): 275 tests across `tests/test_capacity.py`,
`tests/test_capacity_routes.py`, `tests/test_capacity_tab.py` and
`tests/test_capacity_allocation.py`, 399 passing overall with the one pre-existing
date-sensitive failure. P5 is not started. Where the build settled a question the prose
left open, it is marked **as built**.

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
def find_week_cell(row, day) -> week cell or None        # P2: the week an absence lands in
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

**As built (P2).** `POST /projects/<id>/absences` exists and behaves like `manage_holidays`
(form fields `member_id` / `start_date` / `end_date` / `reason`, `partial=settings` returns
`partials/settings_absences.html`, everything else redirects to `#settings`). The form asks
for the *inclusive* pair a person knows — first day away, last day away — and
`parse_absence_range` stores the exclusive end, so the storage convention stays
`Task.end_date`'s. A single day off is one day long; a reversed range, anything over
`ABSENCE_MAX_DAYS = 366`, and an exact duplicate are refused with a plain 400, while a
wider *overlapping* range is allowed because the week maths counts each away day once.
`absence_preview(project)` in the same module answers the one question Settings can
afford to answer on every page render — what is each person carrying across the weeks the
absence list can reach — and is the only capacity object the settings page needs.

**As built (P3).** `GET /projects/<id>/capacity` and
`GET /projects/<id>/capacity/export.csv` are both plain GETs through the same
`capacity_request_args()`, so the file always matches the window on screen. The export is
long format — `Member,Row type,Role,Capacity %,Week,Week starting,Booked days,Available
days,Utilization %,Over days,Tasks`, one row per person per week plus the unowned row and
the weekly team totals — with numbers as numbers and the ratio blank, not infinite, where
a week offers no availability.

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

**As built (P2).** Each member is two forms under one row: the existing name + remove
line, then availability (`role` free text with a `member-roles` datalist of the roles
already in use, `capacity_percent` 0–400 step 5, `cost_per_day`) posting
`action=capacity`. Parsing is strict — `parse_capacity_percent` accepts only whole
numbers, because the field multiplies every week in the plan and a silent rounding rule
would be a planning decision disguised as a typo fix; `parse_cost_per_day` is relaxed
about the decimal separator (`,` or `.`) and rounds to the 2dp the column holds. Neither
save recalculates anything: availability is supply-side annotation, and
`member.cost_per_day` is the 2dp `Numeric(8, 2)` column the plan asked for, read back as a
float (`asdecimal=False`) so a future cost column adds numbers instead of importing
`Decimal`, and `capacity_percent` stays an integer the supply maths divides by 100.

Below the list, `partials/settings_absences.html` holds the absence form and the list of
recorded absences, plus one honest line from `absence_preview`: the weeks that were
checked, and a red percentage beside any absence whose week is already over — the
case where someone is being asked to deliver those days as well as be away. A day off out
of a fully booked week is 125% and says so; a fully booked week that still fits is 100%
and stays quiet.

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

**As built (P3).** Two empty states, because there are two different silences: no team (the
add form says where to set capacity, and the idle capacity is listed under it) and no dated
work (*nothing is dated yet* — the grid still draws, since idle people on a calendar is
the true picture). The line above the grid states the window it computed, the mode, how
many people, how many are over, how much work has no owner, and "…and N more weeks" when
the plan runs past the window.

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

**P4 as built.** The field sits beside *Responsible*, not above it, and the modal row went
from three `col-md-4` fields to four `col-md-3` so the pair reads as one decision. There is
no allocation column and no `·40%` chip on the card: the share is the *Responsible* select's
tooltip, which is where a reader looks when they wonder why one person is booked three times
over. The grid's `⚠` comes from `capacity_overload_map`, which builds the model **once per
grid render** and asks for each task's worst week *inside that task's own window* — a task
that fits in its own weeks is not marked even when its owner is overloaded elsewhere,
because the mark has to be about this row. A milestone is never marked, and neither is
delivered work, an unowned task, a ghost owner or a row without dates: those are not capacity
problems, and the tab already says what they are.

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
| **P2** ✅ | Settings UI: role/capacity %/cost, absences CRUD, `manage_members` `capacity` action | `routes/settings.py`, `routes/capacity.py`, `partials/settings_members.html`, `partials/settings_absences.html` | M |
| **P3** ✅ | Capacity tab: heatmap, load bars, window controls, CSV export, `refreshCapacity` fan-out, `#capacity` deep link | `routes/capacity.py`, `partials/capacity.html`, `project.html`, `style.css` | L |
| **P4** ✅ | Task-level allocation: detail-modal field, grid `⚠` hint, validation, 422 draft round-trip | `routes/tasks.py`, `partials/task_detail_panel.html`, `partials/task_row.html` | M |
| **P5** ✅ | Overview + portfolio KPIs and health rules, Jira columns, seed data, README | `project_overview.py`, `index.html`, `project_overview.html`, `jira_export.py`, `seed_demo.py`, `README.md` | M |

**P4 delivered** (49 tests in `tests/test_capacity_allocation.py`; the capacity family is
275 tests, 399 pass overall, same pre-existing date-sensitive failure):

- The detail modal gained **Allocation** beside *Responsible*: a `0–100` number, step 5,
  placeholder `100`, help text *Share of this person's day while the task runs*. The grid
  row gained no column, as §5.3 asked for.
- One parser guards both percentages now: `parse_percent_field` in `services/common.py`
  takes a whole number in range, treats blank as “not said”, and refuses everything else
  with *`Allocation must be a whole number from 0 to 100.`* `.isdecimal()`, not `isdigit()`:
  a pasted superscript used to reach `int()` and raise. Progress went through the same gate,
  so `progress_percent=150` is now **refused** where it used to be clamped to 100 —
  deliberate, and the clamp was dead code the number inputs never sent.
- A milestone has no share of a person: the input is disabled with *A milestone takes no
  time to run*, and a hand-made post forces `allocation_percent` back to NULL instead of
  trusting the form.
- The grid asks the same engine the tab asks. `capacity_overload_map` in `routes/context.py`
  builds **one** capacity model per grid render (pinned: a test fails if it is built per
  row, or built at all when nothing could be marked), then marks a task only when it has a
  real assignee, real dates, is neither a milestone nor delivered, and that person's **worst
  week inside the task's own window** is over. The row then shows `⚠` with
  *Ann is at 200% in the week of 8 Jun* and the select wears `task-assignee-over`.
- The *Responsible* select carries the share as its tooltip — *40% of this person's time*,
  *Whole of this person's time*, *Nothing: this task asks no time of its owner* — which is
  §5.3's “tooltip showing allocation”, and why there is still no allocation column.
- The inline row form deliberately has **no** allocation field, and saving a row does not
  clear one (test-pinned): the grid row is already the busiest form in the app, and a second
  number in it would compete with the dates that row is about.
- A saved modal fires `refreshCapacity`, so the tab follows the new share.
- One mutation battery of 42 mutants over the parser, the modal save, the map and both
  templates: all killed but two equivalent ones — restoring the old share on a rejected
  post, and dropping the ghost guard — neither of which any request can tell apart.

**P3 delivered** (85 tests in `tests/test_capacity_tab.py`; the capacity trio is 226
tests, 350 pass overall, same pre-existing date-sensitive failure):

- `GET /projects/<id>/capacity` renders `partials/capacity.html` from a `GET`, so Back and
  *Export CSV* follow the window on screen. `?start=` (any date the day helpers accept),
  `?weeks=8|13|26|whole`, `?mode=remaining|full`. A typed number is honoured up to the
  engine's horizon instead of snapped back to the picker, and "whole project" is `None`
  internally: the plan's own span, padded to eight weeks only because a four-week plan
  makes an unreadable grid.
- `app/routes/capacity.py` gained `capacity_context`, `capacity_request_args`,
  `parse_capacity_weeks`, `parse_capacity_mode`, `month_bands`, `capacity_csv` and the two
  routes. `export_stem()` came out of `jira_export.export_filename()` so both exports
  sanitize a project name the same way.
- Grid as specified: sticky name column, month bands, `W23` over `1 Jun` with the
  working-day count in the tooltip, past weeks faded and the current week tinted, cells
  `cap-none/idle/normal/tight/over`, over cells red **and** `▲` **and** a percentage
  (`—` where the week offers no day at all, since "division by zero" is not a reading),
  tooltips naming person / booked / available / task codes, an Unassigned row counted in
  days rather than percentages, and a Team total row.
- Load profile: one CSS bar per member on a scale that stops at 150 %, the tick at 66.7 %
  of the track being one full week, over-loaded bars in the warning gradient.
- **As built, on three points of §5.2.** The row footer moved *into* the sticky column
  (name, capacity badge, role, `booked of available · util`, and a red `N weeks over`
  badge carrying `first_over_date` and the total excess): three numbers under a row read as
  noise when they are the reason the row exists. Ghosts became one sentence under the grid
  instead of `∅` rows, because naming a person with zero availability would hand them a
  percentage. `☤` for "outside the plan window" is not drawn — the default window ends
  with the plan, and a week the reader adds by hand reading as idle is already the truth.
- `refreshCapacity` joins `refreshStats` on every swap that already fired it — task
  writes, project settings, working days, holidays, member availability, absences — and
  the pane listens with `hx-trigger="load, refreshCapacity from:body"`. A **refused** write
  still fires nothing, and that is tested: a rejected predecessor edit leaves the grid be.
- Export is long CSV, one row per person per week plus the unowned row and weekly team
  totals, because a 26-column sheet is unreadable and pivots back to this shape anyway.
  Utilisation is blank where a week has no availability, never `inf`.
- Mutation-checked twice (45 + 24 mutants, all killed). Six mutants were real gaps in the
  first draft of the tests: the singular window note, the current-week mark, the summary
  naming overloaded members and unowned work, the weeks-over badge, booked/available order
  in the row header, and the task list in the cell tooltip.

**P2 delivered** (76 tests in `tests/test_capacity_routes.py`, plus the ones added to
`tests/test_capacity.py` for `find_week_cell` and the forgiving row lookup; suite green
apart from the same pre-existing failure):

- `main.manage_members` `capacity` action, `add` with a starting capacity,
  `parse_capacity_percent` / `parse_cost_per_day`, `ROLE_MAX_LENGTH` truncation.
- `app/routes/capacity.py`: `parse_absence_range`, absence add/delete, the HTMX fragment,
  `absence_preview`, and both halves of the Settings card (templates + CSS).
- `member_roles_in_use` / `absences_in_order` / `parse_flexible_date` in
  `app/services/common.py` — the last one is now also what `parse_holiday_date` uses, so
  holidays and absences accept the same ways of writing a date.
- The preview costs about 7 ms on a stress project (400 tasks, 10 members, 8 weeks), so
  it is computed on every settings render rather than cached — which keeps it honest with
  the "derived, never stored" rule.
- Mutation-checked: window widening, the exclusive absence end, ignoring a pinned `today`,
  warning on any booked week instead of only over ones, the horizon, the beyond-horizon
  list, the week-cell boundary, and each new template branch each fail at least one test.

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

**Written in P2** — `tests/test_capacity_routes.py` (76 tests) drives the real routes with
the test client and checks what the settings card writes: capacity save (blank → 100 and
`None` cost, padded `" 60 "`, whole numbers only — `60%`, `1.5`, `-5`, `401` all 400 and
leave the row untouched), cost with either decimal separator and its `Numeric(8,2)` cap,
role truncation and the de-duplicated/sorted `member-roles` datalist, absence save (four
date spellings, a bare `"Jul 6"` borrowing the current year, the exclusive end), five bad
ranges as plain 400s with the message in the body, duplicate range refused while a wider
overlap is allowed, project scoping of member and absence ids, delete plus 404s, unknown
actions, XSS escaping of names and reasons, cascade with the member, absence dates never
moving a task, and the engine reading back exactly what the forms wrote. The preview has
its own tests: the eight-week window, a far absence widening it, an absence past the
horizon counted instead of dropped, and a warning only for the weeks that are over.

**Written in P3** — `tests/test_capacity_tab.py` (85 tests) reads the tab as a reader
does: member rows, the unowned row, the team total, an over week carrying red + `▲` +
`200%` together, a day off leaving supply at zero and the week still over, quiet weeks not
over, week labels and dates and the working-day tooltip, month bands, ghost names listed
without becoming rows, names escaped, every toolbar control reopening where it was left,
the export link carrying the window, the CSV's numbers as numbers (not `5d`) with a blank
ratio instead of an infinite one, the filename from the project name, empty states, load
bars, tab order, the `#capacity` hash, and the redraw fan-out — task create/delete,
absences, member capacity, holidays and working days all redraw the pane while refused
edits do not.

Written in P4 (`tests/test_capacity_allocation.py`, 49 tests):

- the parse rule, parametrised: `""`, `None`, `" 40 "` accepted; `101`, `-5`, `60%`, `1.5`,
  `twelve` and a superscript refused;
- a share saves as itself, `0` and `100` are both real numbers, an untouched field still
  means the whole person;
- a refused share is **422**, the panel carries the message, the typed value comes back in
  the input, and the stored value is untouched;
- progress is held to the same rule, including the superscript that used to raise a 500;
- a milestone gets no share, by form or by crafted post, and says why;
- a saved share fires `refreshCapacity`; the inline row form neither shows nor clears one;
- the share reaches the engine — two 5-day tasks load 200%, the second at 50% makes it 150%,
  read back off the Capacity tab, and `0%` asks nothing of the week;
- the grid marks a person already over (`⚠`, the sentence, `task-assignee-over`) and leaves a
  row that fits alone; the mark waits for **the task's own weeks**, so a later task for the
  same over-loaded person stays quiet;
- nothing is marked for a ghost owner, an unowned task, a delivered task, a milestone, or a
  task with no dates;
- the tooltip names the week without a leading zero (`the week of 8 Jun`), and the select
  says the share it is asking for;
- no capacity build happens when every owner is a ghost, unowned or undated, and exactly
  **one** build per grid render.

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

**Done as built:** P2 took the feature bullet, the tree (`routes/capacity.py`, the new
test module, the counts), the members row plus the `absences` row in *Routes*, an
absences bullet under *Calendar: working days and holidays*, and the status line. P3 took
a **"Capacity"** section of its own (unit, week bucket, remaining effort, warn-only, the
toolbar and the long CSV), the two capacity rows in *Routes*, the seventh tab, the
`refreshCapacity` fan-out sentence, the rewritten feature bullet, and the counts — 17
test modules, 351 tests, 350 passing. The health-status table still waits for P5, when
there is a rule to describe.

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

**Settled while building P2:**

4. **What clamps what against the calendar.** Settings does not refuse "Friday" for a
   member on a Mon–Wed project, and does not refuse any whole percentage 0–400: supply
   already counts the project calendar, so a 60% member on a 3-day week has 1.8 days, and
   a percentage typed for another project is allowed to look odd here. The refusal the
   question was worried about is not the job of the form; the job of the form is to not
   lie, so the availability list states the weeks it checked and puts a red percentage on
   the one absence whose week is already over. That is the difference between a silent
   60% and a checked one. `parse_capacity_percent` still refuses non-integers, because a
   decimal in a field that multiplies every week is a planning decision, not a typo.

**Still open, for P3:**

5. **Whether the unowned row belongs in `members_over`.** P1 excludes it (it counts
   people), and the P3 tab kept that split honest: `members_over` counts people, the summary
   line adds `N unowned` from `unassigned_demand` right beside it, and the Unassigned row
   gets its own cell shading. So a reader can tell "Ann is over" from "nobody owns this"
   without the two being added into one alarming number. P5 can raise the health status for
   unowned work without changing what the tab means.

**Settled while building P3:**

6. **Toolbar state lives in the query string, not in the pane.** `start`, `weeks` and `mode`
   are query parameters read on every GET, so Back works, a reload keeps the window, the
   CSV export takes the same parameters, and the pane is free to be wholly re-rendered on
   `refreshCapacity`. A `hx-post` form or `hx-vals` hidden state would have made every one
   of those a special case.
7. **Over 100 % is never only a colour.** Each over cell is a background, a `▲` and a
   number; the row gets a badge; the summary counts them. Printing in greyscale and
   colour-blind readers both get the same message, and the percentage means the number is
   not being asked to carry it alone.
8. **The tab is a `GET` partial with no writes of its own.** It changes nothing, so it can
   be re-rendered as often as anything else changes — which is what makes the fan-out
   cheap enough to fire on every task edit.

**Settled while building P4:**

9. **A refused share keeps the old one.** `allocation_percent=150` answers **422** with the
   panel re-rendered, the typed value back in the input and the stored value untouched — the
   same draft round-trip the rest of the modal uses. `progress_percent` went through the new
   parser and therefore stopped clamping: `150` is now a message, not a silent 100.
10. **A milestone owns no time.** The field is disabled, and the save forces NULL whatever
    the form posts, because `duration 0 × allocation` would otherwise turn a delivery gate
    into somebody's week.
11. **The grid pays for one capacity build, not one per row.** `capacity_overload_map`
    returns early when no task could be marked — no members, only ghosts, nothing dated —
    and otherwise builds exactly once. Measured on a worst case where every row is warned:
    the map adds 2 ms at 50 tasks/5 members, 6.5 ms at 200/10, 11.8 ms at 400/10 (the page
    itself goes 92.5 → 104.3 ms at 400 tasks).
12. **Allocation stays out of the inline row form.** The row form has no such field and does
    not clear one, because the grid row is already about names and dates; a third number
    there would compete with both. The modal is where a share is set.

---
**P5 delivered** (400 passing, the date-sensitive failure fixed — the overdue test now
anchors 28 calendar days back, exactly four working weeks, so the slip no longer depends
on the weekday it runs):

- `build_project_overview` takes the whole-plan capacity model and weighs it into health
  last, never first: anyone over is `at_risk`, a sustained crunch (someone over in >2
  weeks, or a week at ≥200 %) is `critical`, and the reason line names the person, the
  weeks and the peak. Capacity can only raise a status, never soften one, and says
  nothing until there is a team to measure.
- The Overview KPI row gained a fifth **Resources** card (`N of M over · peak P%`,
  unowned person-days in the footer, linking to `#capacity`); the row switched to equal
  `col-xl` columns. The portfolio card carries a small `N over capacity` badge per
  project and the Portfolio strip an `over_allocated_count` badge, both counted per
  project-person.
- One capacity build per page render: `main.project` builds the whole-plan model once
  (the pinned grid test still holds) and hands it to both the grid's overload map and
  the overview — `worst_week` only reads the weeks inside each task's own window, so a
  whole-plan model is a superset that marks exactly the same rows.
- Jira export grew `Allocation %` and `Capacity Role` as appended columns, the route
  passing the project's members for the role lookup.
- Seed: 8 members with roles and capacities (one 60 %, one 50 % whose remaining QA load
  peaks at 160 %), 14 allocations across 25/50/100, 3 absences — idle, normal and over
  in one screen; `tests/test_seed_demo.py` still green.

---

**Status:** P1–P5 built and green (400 passed).
**Plan taken from:** this conversation, 10 Oct 2026, against `master` at `a406b00`. The
shape (person-days, ISO weeks, warn-only, per-project, one owner) came from the two
rounds of questions that opened the work.
