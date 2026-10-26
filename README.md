# PERT Swimlane Diagram Builder

A single-user web app for building PERT-style swimlane diagrams. The grid editor is the single source of truth; the diagram is rendered automatically as pure HTML/CSS + SVG.

## Tech Stack

- Python 3.11+
- Flask (app factory)
- HTMX for interactivity
- Bootstrap 5 for UI
- SQLite via Flask-SQLAlchemy
- pywebview (optional desktop window)
- Gunicorn (optional, for running behind a terminal session)
- Pure HTML/CSS + inline SVG for the diagram (no chart libraries)

## Setup

```bash
# Create and activate a virtual environment (do not install packages globally)
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies into the venv
pip install -r requirements.txt

# Run in the browser (LAN-capable)
python run.py
```

### Desktop app (dev)

With the same venv activated:

```bash
python desktop.py
```

This starts Flask on `127.0.0.1` and opens a native window via pywebview.
On Windows, Microsoft Edge WebView2 is required (usually already installed).

### Desktop build (ship to users)

From the project root, with the project `venv` present:

```powershell
.\build_desktop.ps1
```

That installs build deps into the venv and produces:

- `dist\PERTSwimlane\PERTSwimlane.exe` (folder build)
- `dist\PERTSwimlane.zip` (ready to send)

Users unzip and double-click `PERTSwimlane.exe` — no Python install required.
Data is stored under `%LOCALAPPDATA%\PERT Swimlane\`. Recipients still need
WebView2 (preinstalled on most Windows 10/11 machines).

Or with Flask CLI:

```bash
export FLASK_APP=run.py
flask run
```

The app will create `instance/pert.db` in the project directory on first run.

## Running from anywhere else on your network

The app binds to `0.0.0.0` by default, so it is reachable from any device on
the local network at `http://<your-LAN-IP>:5000/`. Start it in the background
(e.g. from another terminal):

```bash
venv/bin/gunicorn --bind 0.0.0.0:5000 --workers 2 "run:app" &
# or simply: python run.py  (foreground)
```

If your system firewall blocks non-standard ports, allow TCP 5000 (on Linux:
`sudo firewall-cmd --add-port=5000/tcp --permanent && sudo firewall-cmd --reload`).

**Cloud dev environments (GitHub Codespaces, etc.):** no extra setup is
needed. The environment forwards ports for you — in Codespaces, open the
**Ports** view (or the *Web App* tab), mark port `5000` as public, and open the
generated `https://…` URL. Keep the server running in a terminal that stays
open (a normal VS Code terminal works).

## Seeding the Demo

To create the 8-task demo project matching the reference image:

```bash
python seed_demo.py
```

Then open http://127.0.0.1:5000/ and click "Demo Project".

## Running Tests

```bash
pytest
```

## Data Model

```
projects (id, name, created_at, updated_at)
   |
   +-- lanes (id, project_id, name, sort_order)
   |
   +-- tasks (id, project_id, code, lane_id, name, start_date, duration_days,
              responsible, predecessors, created_at, updated_at)
```

- `end_date` is derived: `start_date + duration_days` **working days** (exclusive end, 5-day week)
- `code` is auto-assigned as 3-digit zero-padded sequential numbers per project
- `predecessors` stores comma-separated task codes with finish-to-start semantics

## Calendar: 5-day week

Saturday and Sunday are never task time. All weekend arithmetic lives in
`app/workdays.py` (`is_workday`, `next_workday`, `add_workdays`,
`count_workdays`) and everything else goes through it:

- `duration_days` counts Monday-Friday days: a 5-day task starting Monday ends
  the **next Monday**, not Saturday.
- `end_date` (`start_date + duration_days` working days) is the exclusive
  finish - the first day a successor may start - and is always a weekday.
- A start date typed on a weekend is rolled forward to the following Monday,
  both when entered (`parse_date`) and by any recalculation, so the grid never
  holds a Saturday/Sunday date. A task created on a weekend starts on Monday.
- Successors are pushed to `max(predecessor end_date)`, which is already a
  working day, so a cascade never lands on a weekend.
- The project header's "Total effort" and "Duration" are working-day counts.

Example: task 001 starts Thu 1 Jun with duration 3 -> worked Thu 1, Fri 2,
Mon 5, `end_date` Tue 6 Jun; a successor starts Tue 6 Jun.

## Diagram Layout

The diagram is computed server-side with deterministic layout:

1. Lanes render top-to-bottom in `sort_order`
2. Within each lane, tasks sort by `start_date` then `code`, placed left-to-right
3. Tasks in the same lane that share the same (non-empty) predecessor set are
   siblings: they occupy the same column and **stack vertically**. The lane band
   grows to fit the tallest stack. This keeps the shared predecessor's incoming
   arrow from cutting through a sibling card.
4. Cards are HTML/CSS absolutely-positioned divs
5. Dependency arrows are drawn with inline SVG using orthogonal routing

## Validated Rules (Blocking)

1. Start date must be a valid date
2. Duration must be an integer >= 1
3. Predecessors must reference known task codes
4. No cyclic dependencies
5. Task start_date must be >= max(end_date of its predecessors)

The Lane cell is not a blocking rule: an unknown name simply creates the lane.

When blocked, the offending row shows a red error badge with a tooltip explaining the rule.
Dates on the grid are read back after `recalculate_project`, which first rolls
any weekend start date onto Monday and then pushes successors forward along the
dependency chain (only ever later, never earlier).

## Routes

| Method | Path | Description |
|--------|------|-------------|
| GET | `/` | Projects index |
| POST | `/projects` | Create project |
| POST | `/projects/<id>/rename` | Rename project |
| POST | `/projects/<id>/delete` | Delete project |
| GET | `/projects/<id>` | Project page (grid + diagram) |
| POST | `/projects/<id>/lanes` | Lane management: create, rename, delete, and reorder swimlanes from Project settings |
| POST | `/projects/<id>/tasks` | Create task (auto code) |
| POST | `/tasks/<id>/update` | Update task (validated) |
| POST | `/tasks/<id>/delete` | Delete task (cleans predecessors) |
| GET | `/projects/<id>/diagram` | Diagram partial (HTMX) |

## Notes

- The task grid is deliberately low-chrome: one compact line per task, hairline
  row separators, borderless controls that reveal their frame on hover/focus,
  a 3px left bar for the status, and the delete button only on the active row.
  Diagram card styles are scoped under `.task-card` so they never leak into the
  Bootstrap panels (see the "Task grid" block in `app/static/css/style.css`).
- Lanes are managed from Project settings. The grid's **Lane** cell is a simple
  dropdown of the project's existing swimlanes. `+ Add task` on a project with
  no lanes seeds `Lane 1`; otherwise the new row continues the lane of the last
  row. Swimlane order is controlled by the reorder buttons in Project settings.
- Through that route: deleting a lane with tasks moves its tasks to the first
  remaining lane.
- Deleting a task removes its code from all other tasks' predecessor lists.
- The diagram scrolls horizontally for wide projects.
