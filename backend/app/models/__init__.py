"""app.models

SQLAlchemy ORM models.

Notes:
- All model classes are imported here so they register with
  ``Base.metadata`` before ``create_all`` runs.
- ``python -m app.db.bootstrap`` creates tables from this metadata
  on first run (idempotent, Postgres only).
"""

from app.models.battle import Battle, Run
from app.models.model_registry import Model
from app.models.rating import ModelRating
from app.models.task import Task, TaskSet
from app.models.user import User, UserProfile
from app.models.vote import Vote

__all__ = [
    "Battle",
    "Model",
    "ModelRating",
    "Run",
    "Task",
    "TaskSet",
    "User",
    "UserProfile",
    "Vote",
]
