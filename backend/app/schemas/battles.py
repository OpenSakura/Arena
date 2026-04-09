"""app.schemas.battles

Schemas for battles and runs.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

# Allowed battle modes.  Keep in sync with the CHECK constraint on the
# ``battles.mode`` column (``ck_battles_mode``).
ALLOWED_MODES = ("jp2zh_ab",)


class BattleCreate(BaseModel):
    # Optional overrides; defaults chosen server-side.
    task_set_id: str | None = None
    task_id: str | None = None
    mode: Literal["jp2zh_ab"] = "jp2zh_ab"


class RunPublic(BaseModel):
    id: str
    side: Literal["A", "B"]
    output_text: str | None = None
    stats: dict[str, Any] | None = None
    error_text: str | None = None


class BattlePublic(BaseModel):
    id: str
    task_id: str
    source_text: str
    source_lang: str
    target_lang: str
    mode: Literal["jp2zh_ab"]
    status: Literal["pending", "running", "completed", "failed"]
    # Keep model identities hidden until after vote.
    run_a: RunPublic | None = None
    run_b: RunPublic | None = None
