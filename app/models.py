from datetime import datetime, timedelta
from .db import db


class Project(db.Model):
    __tablename__ = "projects"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.Text, nullable=False, unique=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    lanes = db.relationship("Lane", backref="project", lazy=True, cascade="all, delete-orphan")
    tasks = db.relationship("Task", backref="project", lazy=True, cascade="all, delete-orphan")


class Lane(db.Model):
    __tablename__ = "lanes"
    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey("projects.id"), nullable=False)
    name = db.Column(db.Text, nullable=False)
    sort_order = db.Column(db.Integer, nullable=False)

    __table_args__ = (db.UniqueConstraint("project_id", "sort_order", name="uq_lane_order"),)

    tasks = db.relationship("Task", backref="lane", lazy=True)


class Task(db.Model):
    __tablename__ = "tasks"
    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey("projects.id"), nullable=False)
    code = db.Column(db.Text, nullable=False)
    lane_id = db.Column(db.Integer, db.ForeignKey("lanes.id"), nullable=False)
    name = db.Column(db.Text, nullable=False)
    start_date = db.Column(db.Date, nullable=False)
    duration_days = db.Column(db.Integer, nullable=False)
    responsible = db.Column(db.Text, nullable=True)
    predecessors = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (db.UniqueConstraint("project_id", "code", name="uq_task_code"),)

    @property
    def end_date(self):
        return self.start_date + timedelta(days=self.duration_days)

    def predecessor_codes(self):
        if not self.predecessors:
            return []
        codes = [c.strip() for c in self.predecessors.split(",") if c.strip()]
        seen = set()
        result = []
        for c in codes:
            if c not in seen:
                seen.add(c)
                result.append(c)
        return result
