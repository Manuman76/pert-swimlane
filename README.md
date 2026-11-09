# PERT Swimlane

A single-user, local-first project-planning app. Fill in an Excel-like task grid
and the app computes the schedule and renders a read-only PERT swimlane diagram
from the same data. On top of the grid it carries the delivery-management layer
most projects need: milestones, a baselined schedule with working-day variance,
MoSCoW business requirements with traceability, a RAID log, a team directory,
per-project working calendars, and a portfolio dashboard.

**The grid is the single source of truth.** The diagram is a computed, auto
laid-out rendering of the grid; users never drag nodes on a canvas. There are no
accounts, no auth, and no sync — one SQLite file per install.

## What's in it

- **Portfolio dashboard** (`/`) — KPI cards (projects, portfolio progress,
  milestones reached, attention) plus a card per project with health status.
- **Task grid** — columns `ID · Task name · Lane · Start · Days · End ·
  Responsible · BR · Status`; every cell saves on change. Rows render as an
  expandable tree derived from the predecessor graph, with multi-select and a
  drag grip on each row for wiring dependencies.
- **Diagram** — pure HTML/CSS + inline SVG. Lane collapse, lane/status/assignee
  filters, hide-completed, show-requirements, status legend, PNG export.
- **Milestones** — zero-duration checkpoints, rendered as diamonds; the latest
  one is the delivery gate the dashboard measures against.
- **Baseline** — snapshot the schedule, then see signed working-day variance per
  task, per milestone, and for the project.
- **Task detail modal** — name, description, status, assignee, priority,
  progress, lane, dates, linked requirement, notes and links, with predecessors
  and successors listed as jump links. The project description on the Settings
  tab is a Quill rich-text editor; blank rich text is stored as NULL.
- **Drag-and-drop dependencies** — drag the grip of one or many selected tasks
  onto another task to set predecessors; the "Drop here for no predecessor" zone
  clears them. Validation and recalculation run server-side.
- **Business requirements** — `BR-nnn` with MoSCoW priority and a traceability
  matrix whose coverage state is derived from the linked tasks.
- **RAID log** — risks (probability × impact), assumptions, issues (severity,
  due date), decisions (decision, date, rationale).
- **Calendar** — per-project working days and holiday list, used by every date
  calculation.
- **Jira export** — CSV for the Jira importer, with predecessor links.
- **Resource capacity** — a **Capacity** tab of person-days per ISO week: one row per
  team member, one column per week, the cell saying how much of that week is booked.
  Everything in it is computed (`app/capacity.py`) from tasks, the project calendar and
  availability, and nothing about load is stored. Settings carries what it reads: role,
  percent of a person, cost per day and absences (`app/routes/capacity.py`), which lower
  what someone can deliver in a week and never move a task. Over-allocated weeks are
  painted, badged `▲` and exported to CSV, but never blocked. The task-level allocation
  field and the capacity KPIs are the remaining phases in `PLAN-resource-capacity.md`.
- **Desktop app** — same Flask app in a native window (pywebview), plus a
  PyInstaller build script for shipping a Windows folder/zip.

## Tech stack

- Python 3.11+ (developed on 3.14)
- Flask 3.0.3 app factory, Flask-SQLAlchemy 3.1.1, SQLite
- HTMX for all interactivity — no React/Vue, no build step
- Bootstrap 5, vendored under `app/static/vendor/` together with `htmx.min.js`,
  Quill (rich text) and `dom-to-image-more` (PNG export)
- pywebview 6.2.1 (optional desktop window), Gunicorn 23.0.0 (optional WSGI),
  pytest 8.3.2
- Diagram is pure HTML/CSS + inline SVG — no PlantUML, Gantt, dagre/elkjs or
  graph libraries

## Project layout

```
run.py                     dev server entry point (0.0.0.0:5000, debug)
desktop.py                 pywebview window + DesktopApi.save_bytes (PNG save)
seed_demo.py               repeatable demo project
build_desktop.ps1          PyInstaller build (Windows)
pert_swimlane.spec         PyInstaller spec
prompt.md                  original v1 build brief — the product's origin story

app/
  __init__.py              create_app() + _migrate_schema() (inline ALTER TABLE)
  db.py                    the SQLAlchemy instance
  models.py                Project, Task, Lane, Requirement, RaidItem, TeamMember,
                           MemberAbsence, Holiday + choice constants
  workdays.py              WorkdayCalendar + is/next/add/count/diff_workdays
  diagram.py               deterministic layout + SVG arrow routing
  project_overview.py      dashboard metrics and health status
  task_tree.py             predecessor-derived grid tree
  requirement_trace.py     coverage per requirement
  capacity.py              person-day supply vs demand per ISO week (computed, never stored)
  jira_export.py           Jira CSV
  services/
    scheduler.py           recalculate_project, validate_task, baselines, CPM
    common.py              code generators, date/rich-text/enum parsing
  routes/                  single `main` blueprint split by domain
    context.py             blueprint + shared template context helpers
    projects.py            index, project page, overview, settings, baseline, export
    tasks.py               grid CRUD, predecessors, detail modal
    settings.py            lanes, members and availability, working days, holidays
    capacity.py            absence CRUD, the availability preview, the Capacity tab and its CSV
    requirements.py        requirement CRUD
    raid.py                RAID CRUD
  templates/               base, index, project (7 tabs), partials/
  static/css/style.css     all styling (~2k lines)

tests/                     17 test modules, 351 tests
```

## Setup

```bash
# Create and activate a virtual environment (do not install packages globally)
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies into the venv
pip install -r requirements.txt

# Run in the browser (LAN-capable, debug mode)
python run.py
```

Or with the Flask CLI: `export FLASK_APP=run.py && flask run`.

The app creates `instance/pert.db` in the project directory on first run
(`instance/` is gitignored). `db.create_all()` runs at startup, followed by the
column-level migrations in `_migrate_schema()` — just pull and run.

### Desktop app (dev)

With the same venv activated:

```bash
python desktop.py
```

Starts Flask on `127.0.0.1` on a free port and opens a native pywebview window.
On Windows, Microsoft Edge WebView2 is required (usually already installed).

### Desktop build (ship to users)

From the project root, with the project `venv` present:

```powershell
.\build_desktop.ps1
```

That installs the build deps (`requirements-build.txt`, i.e. PyInstaller) into
the venv and produces:

- `dist\PERTSwimlane\PERTSwimlane.exe` (folder build)
- `dist\PERTSwimlane.zip` (ready to send)

Users unzip and double-click `PERTSwimlane.exe` — no Python install required.
The frozen app stores data under `%LOCALAPPDATA%\PERT Swimlane\` on Windows and
`$XDG_DATA_HOME/PERT Swimlane/` (default `~/.local/share/`) elsewhere.
Recipients still need WebView2 (preinstalled on most Windows 10/11 machines).

## Running tests

```bash
pytest
```

`tests/test_desktop_api.py` imports `desktop.py`, which imports `webview`:
install it (`pip install pywebview`) or run
`pytest --ignore=tests/test_desktop_api.py`.

Current status on `master`: **350 passed, 1 failed** (excluding the desktop
module). The failure, `tests/test_project_overview.py::test_overdue_project_health_status`,
is date-sensitive — see [Known issues](#known-issues).

## Seeding a demo project

```bash
python seed_demo.py
```

Creates **"Northstar Commerce Modernization"** (Jun–Dec 2026) — 6 swimlanes,
62 dated tasks with statuses and progress, milestones, a set baseline, 30 MoSCoW
requirements linked to tasks, 17 RAID items, 8 team members, and 5 holidays. It
replaces that demo project if it already exists and leaves every other project
alone, so re-running it is safe.

## Running from anywhere else on your network

The app binds to `0.0.0.0`, so it is reachable from any device on the local
network at `http://<your-LAN-IP>:5000/`. To keep it up in the background:

```bash
venv/bin/gunicorn --bind 0.0.0.0:5000 --workers 2 "run:app" &
# or simply: python run.py  (foreground, debug)
```

If your firewall blocks non-standard ports, allow TCP 5000 (on Fedora:
`sudo firewall-cmd --add-port=5000/tcp --permanent && sudo firewall-cmd --reload`).

**Cloud dev environments (GitHub Codespaces, etc.):** no extra setup is needed.
The environment forwards ports for you — open the **Ports** view, mark port
`5000` as public, and open the generated `https://…` URL.

## Domain model

```
projects (id, name, description, workdays, baseline_set_at, created_at, updated_at)
   +-- lanes        (id, project_id, name, sort_order)      UNIQUE(project_id, sort_order)
   +-- tasks        (id, project_id, code, kind, lane_id, name, start_date,
   |                 duration_days, responsible, predecessors, description,
   |                 notes, links, priority, progress_percent, started, done,
   |                 baseline_start_date, baseline_end_date, baseline_duration_days)
   |                 UNIQUE(project_id, code)   kind: task | milestone
   +-- requirements (id, project_id, code, title, description, moscow, status)
   |                 UNIQUE(project_id, code)   moscow: must|should|could|wont
   +-- raid_items   (id, project_id, kind, code, title, description, owner, status,
   |                 probability, impact, mitigation, severity, due_date,
   |                 decision_date, decision_text, rationale)
   |                 UNIQUE(project_id, code)   kind: risk|assumption|issue|decision
   +-- team_members (id, project_id, name, role, capacity_percent, cost_per_day)
   |                 UNIQUE(project_id, name)
   |   +-- member_absences (id, project_id, member_id, start_date, end_date, reason)
   |                       member_id ON DELETE CASCADE; end_date exclusive
   +-- holidays     (id, project_id, date, name) UNIQUE(project_id, date)

requirement_tasks (requirement_id, task_id)   # many-to-many; the grid links a
                                              # task to at most one requirement
```

- **Codes** are generated per project, not entered: tasks `001`, `002`, …
  (max existing + 1), requirements `BR-001`, `BR-002`, … (count-up, skipping
  taken codes), RAID items per kind — `R-001` risk, `A-001` assumption, `I-001`
  issue, `D-001` decision (max per prefix + 1).
- `predecessors` is a comma-separated list of task **codes** with finish-to-start
  semantics; all reads go through `Task.predecessor_codes()`, which de-duplicates.
- `end_date` is a derived property, never stored: working days for tasks, the
  target date itself for milestones. `member_absences.end_date` follows the same
  exclusive convention.
- `tasks.allocation_percent` and `team_members.capacity_percent` default to NULL and
  100, so every pre-capacity row behaves exactly as it did before: full task, full
  person.
- Status is `done` > `started` > `not_started`; progress is the explicit
  percentage when set, otherwise 100 / 50 / 0 by status.
- Renaming a team member rewrites `tasks.responsible`; deleting one clears the
  assignee. Deleting a lane moves its tasks to the first remaining lane and
  re-packs `sort_order` (the last lane with tasks cannot be deleted).

## Calendar: working days and holidays

Non-working days live in one place: `app/workdays.py`. `WorkdayCalendar` takes a
set of working weekday numbers plus a holiday set, and every date calculation in
the app gets one from `Project.calendar` (project working days + that project's
holidays).

- `duration_days` counts working days only: a 5-day task starting Monday ends the
  **next Monday**, not Saturday.
- `end_date` (`start_date + duration_days` working days) is the exclusive finish —
  the first day a successor may start — and always lands on a working day.
- A start date entered on a non-working day is rolled forward to the next working
  day, both when parsed (`parse_date`) and by any recalculation.
- Changing the working-day pattern or the holiday list recalculates the whole
  project, so a new holiday pushes affected work forward.
- A member's **absences** are calendar too, but supply-side only: they reduce what that
  person can deliver in a week and never move a task, so they never trigger a
  recalculation. Like holidays they are typed freely (`2026-07-06`, `6 Jul 2026`,
  `Jul 6`); unlike holidays they are per person, and the last day away is stored +1 so a
  single day off is one day long, exactly as a task's `end_date` is.
- The Tasks header's **Effort** and **Duration** figures are working-day counts.

Example: task `001` starts Thu 1 Jun with duration 3 → worked Thu 1, Fri 2, Mon 5;
`end_date` is Tue 6 Jun; a successor starts Tue 6 Jun.

## Scheduling

`recalculate_project(project_id)` runs after every change that can move a date:
a grid edit, a task or milestone added, a task deleted, a predecessors drop, and
any working-day or holiday edit.

1. roll every start date onto the next working day;
2. walk the dependency graph in topological order (Kahn's algorithm, roots first);
3. push each task to `max(predecessor end dates)` — already a working day;
4. dates only ever move **forward**: shortening a predecessor never pulls a
   successor earlier.

Tasks with no predecessors keep their entered date and anchor the schedule.
Tasks inside a cycle are left on their entered dates — cycles cannot be saved in
the first place.

### Validated rules (blocking)

1. Start date must be a valid date.
2. Duration must be an integer ≥ 1 — and exactly 0 for milestones.
3. Predecessors must reference known task codes in the same project; no
   self-predecessor; no cycles.
4. Task start must be ≥ max(end date of its predecessors) — **blocking only when
   the user actively moves a start date earlier**. Merely changing the
   predecessor set leaves the stored date alone and lets recalculation push it.
5. Lane must be one of the project's swimlanes (an unknown name is rejected, not
   created).
6. Responsible must be an available team member.
7. A linked business requirement must belong to the project.

Blocked task edits return **HTTP 422 with the re-rendered grid row set or modal**
and the typed values preserved as a draft; the offending row shows a red error
badge with the reason in a tooltip. Simpler form endpoints (create project, add
task with an unknown lane, unknown `action`) answer with plain-text `400`s.

## Baselines, milestones, and variance

`POST /projects/<id>/baseline` snapshots `start_date`, `end_date`, and
`duration_days` into the `baseline_*` columns and stamps `projects.baseline_set_at`;
`action=clear` drops them again. From the snapshot:

- `Task.variance_days` — signed working-day difference (tasks: end vs baseline
  end; milestones: target vs baseline target). Positive is late, shown `+2d`.
- Milestone variance drives `schedule_variance_days`, anchored on the last
  baselined milestone; `schedule_slip_days` is forecast end vs the latest planned
  milestone target.
- The Overview tab and diagram cards surface variance (`+Xd` / `-Xd`) against the
  baseline.

## Overview dashboard

`build_project_overview(tasks, requirements, raid_items, project=…)` aggregates
progress, milestones, requirement coverage, RAID, schedule slip, baseline
variance, and overdue state into one object used by both the project Overview tab
and the portfolio cards. Health:

| Status | When |
|---|---|
| `completed` | all tasks done |
| `critical` ("Needs Attention") | a critical risk (high probability × high impact), or slip / project overdue / milestone overdue **> 5 working days** |
| `at_risk` ("At Risk") | a high risk, any slip, any overdue day or milestone, or requirements with zero coverage |
| `on_track` ("On Track") | otherwise |

Each non-green status carries a one-line reason (`Flagged: …` / `Notice: …`)
listing the contributing factors. `compute_critical_path()` in
`app/services/scheduler.py` implements CPM (forward/backward pass, total float
0) and is covered by tests, but is **not yet rendered anywhere in the UI**.

## Requirements and traceability

The Requirements tab is a traceability matrix, not a second editor: rows are
`BR-nnn` requirements (title, description, MoSCoW), the linked tasks are assigned
from the grid's **BR** column, and coverage is *derived* from task status —
🟥 Not covered → 🟧 Planned → 🟦 In progress → 🟩 Implemented. Search and
priority/coverage filters are client-side; `GET /projects/<id>/requirements/list`
re-renders the partial for HTMX.

## RAID log

One route, four kinds, kind-specific fields: risks carry probability, impact and
mitigation; issues carry severity and a due date; decisions carry the decision,
its date, and the rationale. Status is `open`/`closed`. Critical and high risks
feed the health status; the pane re-renders as an HTMX partial
(`partial=raid`).

## Capacity

One row per team member, one column per ISO week, and the cell is the honest answer to
"how much of that week is booked". Demand is remaining effort
(`duration × allocation × (100 − progress) ÷ 100`) spread over the working days a task
really spans; supply is the calendar's working days × that person's capacity, minus
their absences. Both sides are person-days, because that is the unit the calendar
already counts in — hours would be the same number ×8, and FTE would hide who is away.

Nothing about load is stored. `app/capacity.py` recomputes the grid from tasks,
calendar, capacity and absences on every request, which is why writing any of them
fires `refreshCapacity` instead of patching a number: a cached utilisation would be a
second source of truth, and about 7 ms for 400 tasks is not worth the risk of lying.

Over 100 % is a colour, a `▲` and a badge — never a refusal. A full week is
information, not an error, and the rule in this app is that the plan stays editable.
Work whose owner is not in the team gets an *Unassigned* row, and the name is called
out under the grid rather than invented as a person.

The toolbar picks the window (`?start=`, `?weeks=8|13|26|whole`) and the mode
(remaining effort, or the plan as booked). *Export CSV* downloads that same window as
one row per person per week, so a spreadsheet can pivot it back into the grid on screen
and no one has to read the current window off a URL.

## Diagram

`app/diagram.py::build_diagram()` computes the whole canvas server-side,
deterministically:

1. lanes render top-to-bottom in `sort_order`; a collapsed lane shows a summary band;
2. within a lane, tasks sort by `(start_date, code)` and place left-to-right on a
   fixed card grid (168×108 cards, 52×52 milestone diamonds, 24px gaps);
3. tasks in the same lane sharing an identical predecessor set are siblings: they
   share a column and stack vertically, so the incoming arrow never cuts through
   a sibling card;
4. cards are absolutely-positioned HTML/CSS divs; milestones are diamonds;
5. arrows are inline SVG, routed with a grid A\* search (`heapq` + clearance
   checks) so lines detour around cards instead of crossing them;
6. status drives the left bar and border; variance shows as a `+Xd` chip.

The Diagram tab filters by lane, status and assignee, can hide completed work and
overlay requirement codes, and exports a PNG. `GET /projects/<id>/diagram` is the
HTMX partial; collapsed lanes are passed as `?collapsed=1,3`. Clicking a card
opens the task detail modal.

## Tasks pane

- `+ Add task` / `+ Add milestone` append a row (milestones get `duration 0`) and
  continue in the last row's lane; on a project with no lanes yet, a new task
  seeds `Lane 1`.
- Rows render as a **tree derived from the dependency graph** (`task_tree.py`):
  each task appears once, nested under its lowest-numbered predecessor; cycles
  and unreachable tasks are listed after the forest. Collapse state is per project
  in `sessionStorage`.
- Rows are multi-selectable; dragging a selection's grip onto another task
  assigns the predecessor in one bulk request
  (`/projects/<id>/tasks/predecessors`) with a single recalculation, and dropping
  it on the header zone clears the predecessors instead.
- Every change swaps only `partials/task_table_body.html` and fires
  `refreshDiagram` / `refreshStats` / `refreshCapacity` via `HX-Trigger-After-Swap`, so
  grid, diagram, dashboard and capacity grid stay in sync without a page reload. The
  capacity pane listens for the same fan-out (`hx-trigger="load, refreshCapacity
  from:body"`), because it holds no state of its own to patch.

## Jira export

`GET /projects/<id>/export/jira.csv` returns a file for the Jira CSV importer:
`Summary` (required), `Issue Type`, `Component` (from the lane), `Assignee`,
`Description`, `Due date`, `Original Estimate`, `Labels`, a link column named
`is blocked by` resolved through the shared `Issue ID` column, plus PERT columns
(`PERT Code`, `Start Date`, `Duration (working days)`, `PERT Status`,
`Predecessors`) to map to custom fields. Dates come straight from the scheduler,
so they already follow the project calendar. In the desktop build, CSV download
works because `desktop.py` enables `webview.settings["ALLOW_DOWNLOADS"]`.

## Routes

Single blueprint (`main`); HTMX partials are requested with the usual form fields
plus `partial=…`, and answers carry `HX-Trigger-After-Swap`.

| Method | Path | Description |
|--------|------|-------------|
| GET | `/` | Portfolio dashboard: KPIs + project cards |
| POST | `/projects` | Create project (name, description) |
| POST | `/projects/<id>/rename` | Rename project |
| POST | `/projects/<id>/delete` | Delete project (cascades) |
| GET | `/projects/<id>` | Project page: Overview · Tasks · Diagram · Requirements · RAID · Capacity · Settings |
| GET | `/projects/<id>/overview` | Overview partial (HTMX) |
| POST | `/projects/<id>/settings` | Update name / description |
| POST | `/projects/<id>/baseline` | `action=set` snapshot · `action=clear` drop |
| GET | `/projects/<id>/export/jira.csv` | Jira CSV download |
| GET | `/projects/<id>/diagram` | Diagram partial (`?collapsed=1,3`) |
| POST | `/projects/<id>/tasks` | Add task or milestone (`kind`) |
| POST | `/projects/<id>/tasks/predecessors` | Bulk predecessors for many tasks |
| POST | `/tasks/<id>/update` | Update grid row (validated, 422 + draft on error) |
| POST | `/tasks/<id>/predecessors` | Dependency-only update (drag-and-drop) |
| POST | `/tasks/<id>/delete` | Delete task (scrubs code from successors) |
| GET | `/projects/<id>/task-detail-empty` | Empty task-detail partial |
| GET | `/tasks/<id>/detail` | Task detail modal partial |
| POST | `/tasks/<id>/detail` | Save modal (description, priority, progress, …) |
| POST | `/projects/<id>/lanes` | Lanes: `add` · `rename` · `move_up` · `move_down` · `delete` |
| POST | `/projects/<id>/members` | Team: `add` (optional starting capacity) · `rename` · `capacity` (role, %, cost/day) · `delete` |
| POST | `/projects/<id>/absences` | Availability: `add` (first/last day away) · `delete` |
| POST | `/projects/<id>/workdays` | Working-day pattern (recalculates) |
| POST | `/projects/<id>/holidays` | Holidays: `add` (adds or replaces by date) · `delete` |
| POST | `/projects/<id>/requirements` | Requirements: `add` · `update` · `delete` |
| GET | `/projects/<id>/requirements/list` | Requirements partial |
| POST | `/projects/<id>/raid` | RAID: `add` · `update` · `delete` |
| GET | `/projects/<id>/capacity` | Capacity tab partial (`?start=`, `?weeks=8/13/26/whole`, `?mode=remaining/full`) |
| GET | `/projects/<id>/capacity/export.csv` | The window on screen as CSV: one row per person per week |

## Notes

- **Migrations are hand-rolled.** `db.create_all()` cannot add columns to an
  existing table, so `_migrate_schema()` in `app/__init__.py` issues
  `ALTER TABLE … ADD COLUMN` for every column added since v1. New columns get a
  matching entry there (or existing installs keep failing to boot); there is no
  Alembic.
- `app/routes/__init__.py` is a compatibility shim: it re-exports the blueprint,
  handlers and service helpers (several with leading-underscore aliases) so the
  test suite and seed scripts keep importing from `app.routes`. New code should
  import from the specific module.
- The task grid is deliberately low-chrome: one compact line per task, hairline
  row separators, borderless controls that reveal their frame on hover/focus, a
  3px left bar for the status, and a delete button that only fades in on row
  hover (`#task-table .btn-delete-task`). Diagram card styles are scoped under
  `.task-card` so they never leak into the Bootstrap panels (see the "Task grid"
  block in `app/static/css/style.css`).
- Tabs are deep-linkable (`#overview`, `#tasks`, `#diagram`, `#requirements`,
  `#raid`, `#settings`) and redirects after a full-page form post land back on the
  tab the change came from.
- Third-party JS/CSS is vendored in `app/static/vendor/` — the app makes no
  requests off the machine, which is what makes the desktop build work offline.

### Known issues

- `tests/test_project_overview.py::test_overdue_project_health_status` fails
  depending on the date it runs: its fixture is 10 calendar days old, which is
  exactly 5 working days overdue today, and `critical` needs **more than** 5.
  Widen the fixture or relax the threshold to `>= 5`.
- `pywebview` is in `requirements.txt` but not always installed, which makes
  plain `pytest` abort during collection of `tests/test_desktop_api.py`.
- The suite runs SQLAlchemy 2.x with the 1.x `Query.get()` /
  `Model.query.get()` style; it works but emits ~1.3k legacy warnings. Worth a
  sweep to `db.session.get(Model, id)` before the helper goes away.
