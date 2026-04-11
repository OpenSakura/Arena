"""Drop rating_events table.

The background refresh job recomputes all ratings from scratch on every
cycle, making the per-vote incremental deltas in rating_events historically
inaccurate and misleading.  The table is no longer written to or read from.

Revision ID: 002_drop_rating_events
Revises: 001_merge_params
Create Date: 2026-02-26
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


revision: str = "002_drop_rating_events"
down_revision: str | None = "001_merge_params"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.drop_table("rating_events")


def downgrade() -> None:
    op.create_table(
        "rating_events",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "vote_id",
            UUID(as_uuid=True),
            sa.ForeignKey("votes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "model_a_id",
            UUID(as_uuid=True),
            sa.ForeignKey("models.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "model_b_id",
            UUID(as_uuid=True),
            sa.ForeignKey("models.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("delta_a", sa.Float, nullable=False),
        sa.Column("delta_b", sa.Float, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("vote_id", name="uq_rating_events_vote_id"),
        sa.Index("ix_rating_events_model_a_id", "model_a_id"),
        sa.Index("ix_rating_events_model_b_id", "model_b_id"),
        sa.Index("ix_rating_events_created_at", "created_at"),
    )
