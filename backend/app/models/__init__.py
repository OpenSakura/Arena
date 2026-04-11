"""app.models

SQLAlchemy ORM models.

Notes:
- Import models here so Alembic can discover them for autogeneration.
- Keep model fields stable; changes require migrations.
"""

from app.models.battle import Battle, Run
from app.models.model_registry import Model
from app.models.prompt_template import PromptTemplate
from app.models.rating import ModelRating
from app.models.task import Task, TaskSet
from app.models.user import User, UserProfile
from app.models.vote import Vote

__all__ = [
    "Battle",
    "Model",
    "ModelRating",
    "PromptTemplate",
    "Run",
    "Task",
    "TaskSet",
    "User",
    "UserProfile",
    "Vote",
]
