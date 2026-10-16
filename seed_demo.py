from datetime import date
from app import create_app
from app.db import db
from app.models import Project, Lane, Task
from app.routes import recalculate_project


def seed():
    app = create_app()
    with app.app_context():
        # Clear existing data
        db.session.query(Task).delete()
        db.session.query(Lane).delete()
        db.session.query(Project).delete()
        db.session.commit()

        # Create project
        proj = Project(name="Demo Project")
        db.session.add(proj)
        db.session.commit()

        # Create lanes
        lane_names = ["Customer Account", "Shopping Site", "Shopping Cart", "Testing"]
        lanes = []
        for i, name in enumerate(lane_names, 1):
            lane = Lane(project_id=proj.id, name=name, sort_order=i)
            db.session.add(lane)
            lanes.append(lane)
        db.session.commit()

        lane_map = {l.name: l for l in lanes}

        # Create tasks matching the reference image
        tasks_data = [
            {"code": "001", "name": "Support Account Deletion", "lane": "Customer Account", "start": date(2017, 6, 1), "dur": 3, "resp": "", "preds": ""},
            {"code": "002", "name": "Design a New Theme", "lane": "Shopping Site", "start": date(2017, 6, 1), "dur": 8, "resp": "", "preds": "001"},
            {"code": "003", "name": "Apply New Theme to the Site", "lane": "Shopping Site", "start": date(2017, 6, 9), "dur": 15, "resp": "", "preds": "002"},
            {"code": "004", "name": "Improve Searching", "lane": "Shopping Site", "start": date(2017, 6, 24), "dur": 7, "resp": "", "preds": "003"},
            {"code": "005", "name": "Enhance Shopping Cart Functionality", "lane": "Shopping Cart", "start": date(2017, 6, 9), "dur": 8, "resp": "", "preds": "002"},
            {"code": "006", "name": "Enhance Shopping Cart Checkout", "lane": "Shopping Cart", "start": date(2017, 6, 17), "dur": 6, "resp": "", "preds": "005"},
            {"code": "007", "name": "Ready Testing Environment", "lane": "Testing", "start": date(2017, 7, 3), "dur": 2, "resp": "", "preds": "004,006"},
            {"code": "008", "name": "Test Online Shop", "lane": "Testing", "start": date(2017, 7, 5), "dur": 8, "resp": "", "preds": "007"},
        ]

        tasks = []
        for td in tasks_data:
            task = Task(
                project_id=proj.id,
                code=td["code"],
                lane_id=lane_map[td["lane"]].id,
                name=td["name"],
                start_date=td["start"],
                duration_days=td["dur"],
                responsible=td["resp"] or None,
                predecessors=td["preds"] or None,
            )
            db.session.add(task)
            tasks.append(task)
        db.session.commit()

        # Settle the schedule: 004/006 are entered on a Saturday and the
        # calendar is a 5-day week, so their dates (and everything downstream)
        # roll onto the next working days before the demo is shown.
        recalculate_project(proj.id)
        db.session.commit()

        print(f"Seeded project '{proj.name}' with {len(lanes)} lanes and {len(tasks)} tasks.")


if __name__ == "__main__":
    seed()
