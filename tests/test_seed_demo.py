import re

from app import create_app
from app.db import db
from app.models import Lane, Project, RaidItem, Requirement, Task
from app.diagram import build_diagram
from app.services.scheduler import validate_task
from seed_demo import PROJECT_DUE, PROJECT_NAME, PROJECT_START, seed


def test_seed_demo_builds_complete_scheduled_sample():
    app = create_app({
        "TESTING": True,
        "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
    })
    with app.app_context():
        db.create_all()
        other = Project(name="Keep this project")
        db.session.add(other)
        db.session.commit()
        other_id = other.id

        seed(app)

        project = Project.query.filter_by(name=PROJECT_NAME).one()
        tasks = Task.query.filter_by(project_id=project.id).all()
        requirements = Requirement.query.filter_by(project_id=project.id).all()
        raid_items = RaidItem.query.filter_by(project_id=project.id).all()
        by_code = {task.code: task for task in tasks}

        assert len(tasks) >= 50
        assert len(requirements) >= 25
        assert sum(item.kind == "decision" for item in raid_items) >= 1
        assert sum(item.kind == "issue" for item in raid_items) >= 1
        assert min(task.start_date for task in tasks) == PROJECT_START
        assert by_code["062"].start_date == PROJECT_DUE
        assert by_code["062"].is_milestone
        assert project.baseline_set_at is not None
        assert all(requirement.tasks for requirement in requirements)
        assert Project.query.get(other_id) is not None
        assert all(not validate_task(task, project.id) for task in tasks)

        html = build_diagram(
            Lane.query.filter_by(project_id=project.id).all(),
            tasks,
            members=project.members,
        )
        cards = {
            code: tuple(map(float, (x, y, width, height)))
            for x, y, width, height, code in re.findall(
                r'class="task-card[^"]*" style="left:([\d.]+)px; top:([\d.]+)px; '
                r'width:([\d.]+)px; height:([\d.]+)px;">[\s\S]*?'
                r'<div class="card-cell">(\d+)</div>',
                html,
            )
        }
        arrows = re.findall(
            r'<path d="([^"]+)"[^>]*data-src="([^"]+)" data-dst="([^"]+)"',
            html,
        )
        assert len(cards) >= 50
        assert len(arrows) >= 100

        def segment_crosses_card(start, end, rect):
            x1, y1 = start
            x2, y2 = end
            rx, ry, width, height = rect
            left, right = rx + 1, rx + width - 1
            top, bottom = ry + 1, ry + height - 1
            if x1 == x2:
                return left < x1 < right and min(y1, y2) < bottom and max(y1, y2) > top
            if y1 == y2:
                return top < y1 < bottom and min(x1, x2) < right and max(x1, x2) > left
            return False

        for path, source, target in arrows:
            points = [
                tuple(map(float, point))
                for point in re.findall(r"[ML] ([\d.]+) ([\d.]+)", path)
            ]
            for start, end in zip(points, points[1:]):
                for code, rect in cards.items():
                    if code not in (source, target):
                        assert not segment_crosses_card(start, end, rect), (
                            f"arrow {source} -> {target} crosses task {code}"
                        )

        seed(app)
        assert Project.query.filter_by(name=PROJECT_NAME).count() == 1
        assert Task.query.filter_by(project_id=project.id).count() == len(tasks)
        assert Project.query.get(other_id) is not None

        db.session.remove()
        db.drop_all()
