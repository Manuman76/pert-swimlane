# Build a PERT/Swimlane Diagram Builder (Flask + HTMX + Bootstrap + SQLite)

## Goal
Build a single-user web app where a user fills in an Excel-like grid of tasks and the app
auto-renders a PERT-style swimlane diagram (read-only) that looks like the reference image.
The GRID is the single source of truth. The diagram is a computed, auto-laid-out rendering
of the grid data. Users never drag nodes on the canvas.

## Tech stack (do not deviate)
- Python 3.11+
- Flask (app factory pattern)
- HTMX for all interactivity (no React/Vue)
- Bootstrap 5 for the UI theme (use a clean, light Bootstrap theme)
- SQLite for storage (via SQLAlchemy or stdlib sqlite3 — your pick, keep it simple)
- The diagram is rendered as pure HTML/CSS + an inline <svg> overlay for arrows.
  Do NOT use PlantUML, Gantt libraries, dagre/elkjs, Cytoscape, or any graph/plotting lib.
- No accounts, no auth, no real-time collaboration, no file import/export. Database only.

## Scope (v1)
IN:
- Project index page: create / rename / delete projects.
- Project page: the task grid editor + the auto-rendered swimlane diagram.
- Lanes managed inline in the grid via a "Lane" column (order preserved as entered).
OUT (do not build):
- Manual canvas dragging / manual node positioning.
- Accounts, sharing, permissions, concurrent editing.
- Excel/CSV/JSON import or export.
- Real-time collaboration.

## Data model (SQLite)
projects
  id            PK
  name          TEXT unique, not null
  created_at, updated_at  DATETIME

lanes
  id            PK
  project_id    FK -> projects.id
  name          TEXT not null
  sort_order    INTEGER not null      (order as entered; lanes render top→bottom)
  unique(project_id, sort_order)

tasks
  id            PK
  project_id    FK -> projects.id
  code          TEXT not null         -- "001","002",... auto-assigned (see below)
  lane_id       FK -> lanes.id
  name          TEXT not null
  start_date    DATE not null
  duration_days INTEGER not null, >= 1
  responsible   TEXT (nullable)       -- free-text owner/assignee
  predecessors  TEXT (nullable)       -- comma-separated task codes, e.g. "002, 004"
  created_at, updated_at  DATETIME
  unique(project_id, code)

Derived (never stored):
  end_date = start_date + duration_days   (EXCLUSIVE end).
  Verified against the reference image, every task uses this:
    001: 1 Jun + 3d -> 4 Jun   |  002: 1 Jun + 8d -> 9 Jun   |  003: 9 Jun + 15d -> 24 Jun
    004: 24 Jun + 7d -> 1 Jul  |  005: 9 Jun + 8d -> 17 Jun  |  006: 17 Jun + 6d -> 23 Jun
    007: 3 Jul + 2d -> 5 Jul   |  008: 5 Jul + 8d -> 13 Jul

## Task IDs ("code")
- Auto-assigned as 3-digit zero-padded sequential numbers ("001","002",...) in the order
  tasks are added within a project.
- IDs are NEVER user-typed. The grid shows them in a read-only "ID" column.
- Users reference IDs only inside the Predecessors column.
- On delete, do NOT renumber; keep codes stable. New tasks continue the sequence (next max + 1).

## Dependencies ("Predecessors" column)
- Free-text input of comma-separated task codes, e.g. "002, 004".
- Finish-to-start semantics: a task cannot start before its predecessor's end_date.
- Live validation (on input and on save):
  - Unknown code (references a non-existent task) -> error.
  - Cyclic dependency (a task is its own ancestor) -> error.
- Store exactly what the user typed; normalize whitespace and sort codes when rendering.

## Validation (BLOCKING)
The app must REJECT saving a task when any of these is true and show a clear inline error:
  1. start_date missing or not a valid date.
  2. duration_days < 1 or not an integer.
  3. predecessor references an unknown task code.
  4. predecessor cycle detected (any new/updated task introduces a cycle).
  5. Scheduling violation: task.start_date < max(end_date of its predecessors).
     (i.e. a task starts before a predecessor finishes.)
When blocked, the offending row stays in the grid with a red error badge and a tooltip
explaining the rule. Do not silently drop data.

## Pages & routes
- GET  /                     -> projects index (table: name, updated_at, #tasks, actions)
- POST /projects             -> create project (name)
- POST /projects/<id>/rename -> rename
- POST /projects/<id>/delete -> delete (with confirm; cascades lanes + tasks)
- GET  /projects/<id>        -> project page (grid editor + rendered diagram)
- POST /projects/<id>/lanes   -> add/rename/reorder/delete lane (drives the Lane column values)
- POST /projects/<id>/tasks   -> create task (auto-assigns code, runs validation)
- POST /tasks/<id>/update     -> update task fields (runs validation)
- POST /tasks/<id>/delete     -> delete task (also cleans other tasks' predecessor lists that
                                 referenced it, with a notice)
All mutating actions go through HTMX; return either the updated grid partial, the updated
diagram partial, or a validation error partial (re-renders just the affected row). The
diagram partial re-renders on any task/lane change.

## The grid editor (source of truth)
An Excel-like table, one row per task, columns (left→right):
  [ ID (read-only, auto) | Task name | Lane (select) | Start date | Duration (days)
    | End date (read-only, computed) | Responsible | Predecessors (comma codes) | Actions ]
- Lane column: a <select> of the project's lanes (in sort order). Reordering lanes is done via
  a small lane manager (add/rename/move up-down/delete) above or beside the grid.
- End date column: computed read-only from start + duration.
- Predecessors column: text input with live validation; show a small badge listing resolved
  predecessor names (or a red badge for errors).
- Add-task button adds an empty row (code auto-assigned on save).
- Editing any cell via HTMX updates only that row + re-renders the diagram partial.
- Keep it keyboard-friendly and fast; no full-page reloads.

## The auto-rendered diagram (pure HTML/CSS + inline SVG)
Reproduce the reference image's look. This is a READ-ONLY rendering computed server-side
(Flask) and embedded in the page as HTML (for lanes + cards) plus one <svg> overlay (for arrows).

Layout algorithm (deterministic, no user control):
  1. Group tasks by lane. Render lanes top→bottom in `sort_order`.
  2. Within each lane, sort tasks by start_date, tie-break by code. Place them left→right in
     that order. This left→right / top→bottom ordering is what makes dependency arrows flow
     naturally (earlier tasks on the left / above, later tasks on the right / below).
  3. Grid geometry (fixed constants, tuned to match the image):
       card_w = 168px, card_h = 92px, gap_x = 24px, lane_h = 140px, lane_gap = 16px,
       lane_label_w = 120px (left gutter for the lane name).
     x(task) = lane_label_w + index_in_lane * (card_w + gap_x)
     y(task) = header_h + lane_index * (lane_h + lane_gap)
     Canvas width = max over lanes of (label + n_tasks*(card_w+gap_x)) + right margin.
  4. Lane bands: each lane is a full-width horizontal band (subtle alternating background or a
     1px separator line) with the lane NAME in the left gutter, vertically centered, like the
     image ("Customer Account", "Shopping Site", "Shopping Cart", "Testing").
  5. Top annotation strip (match the image): a thin header row with three centered labels
     "Task", "Dependency", "Lane" each with a small downward arrow, in a muted/red accent.
     This is decorative (documents what the diagram shows) and sits above the lanes.

Task card (a small table, exactly like the image), ONE single theme color for ALL cards:
  - Row 1 (header bar): rounded top corners, filled with the theme color, white text, task name
    (truncated with title-attr). 
  - Row 2: two cells — task code (e.g. "001") | duration (e.g. "3 days").
  - Row 3: two cells — start date | end date, formatted "1 Jun 2017".
  - Row 4: single cell, centered, small grey text — the Responsible value (or empty).
  - Thin border, white background, subtle shadow. Same card geometry for every task.

Arrows (the SVG overlay, one per dependency A -> B):
  - Draw a FINISH-to-START arrow from A to B using orthogonal (elbow) connectors only
    (horizontal + vertical segments, no diagonals), with a filled arrowhead marker at the end.
  - Anchor points: leave A from its RIGHT edge midpoint (or BOTTOM if B is in a lower lane and
    the path would cross cards); enter B at its LEFT edge midpoint (or TOP if coming from above).
  - Routing heuristic (keep it simple and deterministic):
      * Same lane, B to the right of A: a single straight horizontal line in the gap between them.
      * Different lane (B below A): from A's right edge go horizontal a short stub into the
        inter-lane gap, then vertical down through the gap, then horizontal into B's left edge.
        Route the vertical segment in the horizontal middle of the gap between A and B so it
        clears the cards; nudge (offset) collinear segments by a few px if two arrows overlap.
      * Different lane (B above A, i.e. "backward" dependency): route around the outside of the
        canvas (far right or far left margin) so it doesn't cross cards; render these in a
        distinct style (dashed) to signal out-of-order dependencies.
  - Arrows must never pass through a task card. If the simple heuristic can't guarantee that,
    prefer a longer path in the outer margins over crossing a card.
  - Color: neutral dark grey for normal arrows; a distinct accent for backward (out-of-order) ones.

Rendering implementation notes:
  - Compute all coordinates in Python, then:
      * Emit lanes + cards as positioned HTML (CSS grid or absolutely-positioned divs).
      * Emit a single absolutely-positioned <svg> (same pixel dimensions as the canvas) on top
        with pointer-events:none, containing only the arrows.
  - Wrap the whole diagram in a horizontally-scrollable container (overflow-x:auto) so wide
    projects scroll instead of clipping.
  - Provide a lightweight zoom (CSS transform: scale) with +/– and fit buttons if trivial;
    optional, not required.

## Date handling
- Accept and display dates as "1 Jun 2017" (d Mmm yyyy) to match the image.
- Store as ISO dates in SQLite.
- end_date = start_date + duration_days (EXCLUSIVE). Show end_date in both grid and card.
- All scheduling validation uses these computed end_dates.

## UI/UX details
- Bootstrap 5, light theme, consistent spacing. Grid and diagram on one page: grid on top (or
  left) and the diagram below, both re-rendered via HTMX partials on change.
- Inline validation errors (red badge + tooltip) per row; a project-level banner if a save was
  blocked.
- Empty states: "No lanes yet" / "No tasks yet" with helpful hints.
- Delete confirmations (project, lane, task) via a confirm dialog (HTMX or native confirm()).
- When a lane is deleted, block it if it still has tasks (require moving/deleting them first),
  OR move its tasks to a default lane — pick one and document it.
- Keep the app fully usable without JavaScript beyond HTMX's core (HTMX is allowed; no other
  front-end framework).

## Acceptance criteria (must pass)
1. A user can create a project, add lanes, and add the 8 tasks from the reference image via the
   grid. Saving is blocked with clear messages until all data is valid.
2. Entering the image's exact data (codes 001–008, lanes in order, start dates, durations, and
   predecessor links: 001->002; 002->003; 003->004; 002->005; 005->006; 004->007; 006->008)
   renders a swimlane diagram matching the reference: 4 lanes, cards ordered by start date
   within each lane, and dependency arrows with the correct elbow routing.
3. Editing any task (name, dates, duration, lane, predecessors, responsible) updates the grid
   row and re-renders the diagram via HTMX with no full page reload.
4. A scheduling violation (task start before a predecessor's end) is blocked with a message.
   A cycle (A->B->A) is blocked with a message. An unknown predecessor code is blocked.
5. Deleting a task removes its code and cleans up other tasks' predecessor lists.
6. The diagram never renders with arrows passing through a task card for the 8-task scenario.
7. No external chart/graph/PlantUML library is used; diagram is HTML/CSS + inline SVG only.

## Definition of done
- `README.md` with setup (deps, sqlite db creation, run instructions) and a short data-model
  diagram.
- Runs with `pip install -r requirements.txt` and `flask run` (or an equivalent documented command).
- A seeded demo project containing the 8 reference tasks (via a `seed_demo.py` or similar) so
  the reviewer can immediately see the image reproduced.
- Basic tests (pytest) covering: end_date math, validation rules (unknown/cyclic/scheduling),
  code auto-assignment, and layout coordinate computation.
