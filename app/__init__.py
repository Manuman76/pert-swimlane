from flask import Flask
from sqlalchemy import text
from .db import db


def create_app(config=None):
    app = Flask(__name__)
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
