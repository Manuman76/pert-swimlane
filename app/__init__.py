import os
import sys
from pathlib import Path

from flask import Flask
from sqlalchemy import text

from .db import db


def _is_frozen() -> bool:
    return getattr(sys, "frozen", False)


def _user_data_dir() -> Path:
    if sys.platform == "win32":
        root = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    else:
        root = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    path = root / "PERT Swimlane"
    path.mkdir(parents=True, exist_ok=True)
    return path


def create_app(config=None):
    flask_kwargs = {}
    if _is_frozen():
        base = Path(sys._MEIPASS)
        flask_kwargs["template_folder"] = str(base / "app" / "templates")
        flask_kwargs["static_folder"] = str(base / "app" / "static")
        flask_kwargs["instance_path"] = str(_user_data_dir())

    app = Flask(__name__, **flask_kwargs)
    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///pert.db"
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    app.config["SECRET_KEY"] = "pert-secret-key"
    if config:
        app.config.update(config)

    db.init_app(app)

    from . import routes
    app.register_blueprint(routes.bp)

    with app.app_context():
        db.create_all()
        _migrate_schema()

    return app


def _migrate_schema():
    """Add columns that db.create_all() cannot add to existing tables."""
    if db.engine.dialect.name != "sqlite":
        return
    existing = {r[1] for r in db.session.execute(text("PRAGMA table_info(tasks)"))}
    for col in ("started", "done"):
        if col not in existing:
            db.session.execute(
                text(f"ALTER TABLE tasks ADD COLUMN {col} BOOLEAN NOT NULL DEFAULT 0")
            )

    project_columns = {r[1] for r in db.session.execute(text("PRAGMA table_info(projects)"))}
    if "description" not in project_columns:
        db.session.execute(text("ALTER TABLE projects ADD COLUMN description TEXT"))

    db.session.commit()
