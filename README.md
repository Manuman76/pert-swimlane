# PERT Swimlane Diagram Builder

A single-user web app for building PERT-style swimlane diagrams. The grid editor is the single source of truth; the diagram is rendered automatically as pure HTML/CSS + SVG.

## Tech Stack

- Python 3.11+
- Flask (app factory)
- HTMX for interactivity
- Bootstrap 5 for UI
- SQLite via Flask-SQLAlchemy
- Pure HTML/CSS + inline SVG for the diagram (no chart libraries)

## Setup

```bash
# Create and activate a virtual environment
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Run the app
python run.py
```

Or with Flask CLI:

```bash
export FLASK_APP=run.py
flask run
```

The app will create `instance/pert.db` in the project directory on first run.

## Running from anywhere on your network

The app binds to `0.0.0.0` by default, so it is reachable from any device on the local network at `http://<your-LAN-IP>:5000/`.

Start it in the background (e.g. from another terminal on this machine):

```bash
venv/bin/gunicorn --bind 0.0.0.0:5000 --workers 2 "run:app" &
# or simply: python run.py  (foreground)
```

Note: the firewall on this system already allows TCP ports 1025-65535, so port 5000 is open.

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

- `end_date` is derived: `start_date + duration_days` (exclusive end)
- `code` is auto-assigned as 3-digit zero-padded sequential numbers per project
- `predecessors` stores comma-separated task codes with finish-to-start semantics

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

When blocked, the offending row shows a red error badge with a tooltip explaining the rule.

## Routes

| Method | Path | Description |
|--------|------|-------------|
| GET | `/` | Projects index |
| POST | `/projects` | Create project |
| POST | `/projects/<id>/rename` | Rename project |
| POST | `/projects/<id>/delete` | Delete project |
| GET | `/projects/<id>` | Project page (grid + diagram) |
| POST | `/projects/<id>/lanes` | Add/rename/move/delete lanes |
| POST | `/projects/<id>/tasks` | Create task (auto code) |
| POST | `/tasks/<id>/update` | Update task (validated) |
| POST | `/tasks/<id>/delete` | Delete task (cleans predecessors) |
| GET | `/projects/<id>/diagram` | Diagram partial (HTMX) |

## Notes

- Deleting a lane with tasks moves its tasks to the first remaining lane.
- Deleting a task removes its code from all other tasks' predecessor lists.
- Lanes are reordered via small up/down buttons in the lane manager.
- The diagram scrolls horizontally for wide projects.
