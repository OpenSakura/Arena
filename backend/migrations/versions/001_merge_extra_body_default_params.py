"""Merge extra_body and default_params into single params column.

Revision ID: 001_merge_params
Revises: 0001_initial_schema
Create Date: 2026-02-26
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


revision: str = "001_merge_params"
down_revision: str | None = "0001_initial_schema"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    # 1. Add the new `params` column.
    op.add_column("models", sa.Column("params", JSONB, nullable=True))

    # 2. Merge existing data: default_params is the base, extra_body overwrites.
    #    This preserves the old runtime merge semantics.
    op.execute(
        """
        UPDATE models
        SET params = COALESCE(default_params, '{}'::jsonb) || COALESCE(extra_body, '{}'::jsonb)
        WHERE default_params IS NOT NULL OR extra_body IS NOT NULL
        """
    )

    # 3. Drop the old columns.
    op.drop_column("models", "extra_body")
    op.drop_column("models", "default_params")


def downgrade() -> None:
    # Re-create both columns (data is placed in default_params; extra_body left null).
    op.add_column("models", sa.Column("default_params", JSONB, nullable=True))
    op.add_column("models", sa.Column("extra_body", JSONB, nullable=True))

    op.execute(
        """
        UPDATE models
        SET default_params = params
        WHERE params IS NOT NULL
        """
    )

    op.drop_column("models", "params")
