"""Initial schema.

This revision bootstraps the database schema for a fresh install.

Revision ID: 0001_initial_schema
Revises: None
Create Date: 2026-02-26
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID


revision: str = "0001_initial_schema"
down_revision: str | None = None
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    # ---------------------------------------------------------------------
    # Core tables
    # ---------------------------------------------------------------------

    op.create_table(
        "prompt_templates",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("template_text", sa.Text(), nullable=False),
        sa.Column("input_schema", JSONB, nullable=True),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "name",
            "version",
            name="uq_prompt_templates_name_version",
        ),
        sa.CheckConstraint("version >= 1", name="ck_prompt_templates_version_positive"),
    )
    op.create_index(
        "ix_prompt_templates_content_hash",
        "prompt_templates",
        ["content_hash"],
    )

    # NOTE: this is the pre-params schema. The next revision merges
    # `default_params` + `extra_body` into a single `params` column.
    op.create_table(
        "models",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("display_name", sa.String(length=128), nullable=False),
        sa.Column("provider_type", sa.String(length=64), nullable=False),
        sa.Column("model_name", sa.String(length=128), nullable=False),
        sa.Column("base_url", sa.Text(), nullable=False),
        sa.Column(
            "enabled",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.Column(
            "visibility",
            sa.String(length=32),
            server_default=sa.text("'public'"),
            nullable=False,
        ),
        sa.Column("tags", JSONB, nullable=True),
        sa.Column("temperature", sa.Float(), nullable=True),
        sa.Column("frequency_penalty", sa.Float(), nullable=True),
        sa.Column("presence_penalty", sa.Float(), nullable=True),
        sa.Column("default_params", JSONB, nullable=True),
        sa.Column("extra_body", JSONB, nullable=True),
        sa.Column(
            "prompt_template_id",
            UUID(as_uuid=True),
            sa.ForeignKey("prompt_templates.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("encrypted_api_key", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "provider_type",
            "model_name",
            name="uq_models_provider_model",
        ),
        sa.CheckConstraint(
            "visibility IN ('public', 'private')",
            name="ck_models_visibility",
        ),
        sa.CheckConstraint(
            "temperature IS NULL OR (temperature >= 0 AND temperature <= 2)",
            name="ck_models_temperature",
        ),
        sa.CheckConstraint(
            "frequency_penalty IS NULL OR (frequency_penalty >= -2 AND frequency_penalty <= 2)",
            name="ck_models_frequency_penalty",
        ),
        sa.CheckConstraint(
            "presence_penalty IS NULL OR (presence_penalty >= -2 AND presence_penalty <= 2)",
            name="ck_models_presence_penalty",
        ),
    )
    op.create_index(
        "ix_models_enabled_visibility",
        "models",
        ["enabled", "visibility"],
    )
    op.create_index(
        "ix_models_prompt_template_id",
        "models",
        ["prompt_template_id"],
    )
    op.create_index(
        "ix_models_model_name",
        "models",
        ["model_name"],
    )

    op.create_table(
        "users",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("oidc_issuer", sa.String(length=512), nullable=False),
        sa.Column("oidc_sub", sa.String(length=512), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "oidc_issuer",
            "oidc_sub",
            name="uq_users_oidc_issuer_sub",
        ),
    )

    op.create_table(
        "user_profiles",
        sa.Column(
            "user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("display_name", sa.String(length=128), nullable=True),
        sa.Column("ui_language", sa.String(length=32), nullable=True),
        sa.Column("zh_variant", sa.String(length=32), nullable=True),
        sa.Column("jp_proficiency", JSONB, nullable=True),
        sa.Column("translation_experience", JSONB, nullable=True),
        sa.Column("consents", JSONB, nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )

    op.create_table(
        "task_sets",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("metadata", JSONB, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("name", name="uq_task_sets_name"),
    )

    op.create_table(
        "tasks",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "task_set_id",
            UUID(as_uuid=True),
            sa.ForeignKey("task_sets.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "source_lang",
            sa.String(length=16),
            server_default=sa.text("'ja'"),
            nullable=False,
        ),
        sa.Column(
            "target_lang",
            sa.String(length=16),
            server_default=sa.text("'zh'"),
            nullable=False,
        ),
        sa.Column("source_text", sa.Text(), nullable=False),
        sa.Column("metadata", JSONB, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_tasks_task_set_id", "tasks", ["task_set_id"])
    op.create_index(
        "ix_tasks_source_target_lang",
        "tasks",
        ["source_lang", "target_lang"],
    )

    op.create_table(
        "battles",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "task_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tasks.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "mode",
            sa.String(length=64),
            server_default=sa.text("'jp2zh_ab'"),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.String(length=32),
            server_default=sa.text("'pending'"),
            nullable=False,
        ),
        sa.Column("metadata", JSONB, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'completed', 'failed')",
            name="ck_battles_status",
        ),
    )
    op.create_index("ix_battles_task_id", "battles", ["task_id"])
    op.create_index(
        "ix_battles_status_created_at",
        "battles",
        ["status", "created_at"],
    )

    op.create_table(
        "runs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "battle_id",
            UUID(as_uuid=True),
            sa.ForeignKey("battles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("side", sa.String(length=1), nullable=False),
        sa.Column(
            "model_id",
            UUID(as_uuid=True),
            sa.ForeignKey("models.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("request_json", JSONB, nullable=True),
        sa.Column("prompt_rendered", JSONB, nullable=True),
        sa.Column("output_text", sa.Text(), nullable=True),
        sa.Column("output_text_raw", sa.Text(), nullable=True),
        sa.Column("stats", JSONB, nullable=True),
        sa.Column("error_text", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("battle_id", "side", name="uq_runs_battle_side"),
        sa.CheckConstraint("side IN ('A', 'B')", name="ck_runs_side"),
    )
    op.create_index("ix_runs_battle_id", "runs", ["battle_id"])
    op.create_index("ix_runs_model_id", "runs", ["model_id"])

    op.create_table(
        "votes",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "battle_id",
            UUID(as_uuid=True),
            sa.ForeignKey("battles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("winner", sa.String(length=8), nullable=False),
        sa.Column("rubric", JSONB, nullable=True),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column(
            "voter_user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("voter_anon_id", sa.String(length=64), nullable=True),
        sa.Column("ip_hash", sa.String(length=128), nullable=True),
        sa.Column("user_agent_hash", sa.String(length=128), nullable=True),
        sa.Column(
            "revealed",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint("winner IN ('A', 'B', 'tie')", name="ck_votes_winner"),
        sa.CheckConstraint(
            "(voter_user_id IS NOT NULL) OR (voter_anon_id IS NOT NULL)",
            name="ck_votes_identity_present",
        ),
    )
    op.create_index(
        "uq_votes_battle_voter_user",
        "votes",
        ["battle_id", "voter_user_id"],
        unique=True,
        postgresql_where=sa.text("voter_user_id IS NOT NULL"),
    )
    op.create_index(
        "uq_votes_battle_voter_anon",
        "votes",
        ["battle_id", "voter_anon_id"],
        unique=True,
        postgresql_where=sa.text("voter_anon_id IS NOT NULL"),
    )
    op.create_index("ix_votes_battle_id", "votes", ["battle_id"])
    op.create_index("ix_votes_voter_user_id", "votes", ["voter_user_id"])
    op.create_index("ix_votes_voter_anon_id", "votes", ["voter_anon_id"])
    op.create_index("ix_votes_ip_hash", "votes", ["ip_hash"])
    op.create_index("ix_votes_created_at", "votes", ["created_at"])

    op.create_table(
        "model_ratings",
        sa.Column(
            "model_id",
            UUID(as_uuid=True),
            sa.ForeignKey("models.id", ondelete="RESTRICT"),
            primary_key=True,
        ),
        sa.Column(
            "rating",
            sa.Float(),
            server_default=sa.text("1000.0"),
            nullable=False,
        ),
        sa.Column(
            "games_played",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_model_ratings_rating", "model_ratings", ["rating"])

    # Pre-deprecation table (dropped in the next revision).
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
        sa.Column("delta_a", sa.Float(), nullable=False),
        sa.Column("delta_b", sa.Float(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("vote_id", name="uq_rating_events_vote_id"),
    )
    op.create_index("ix_rating_events_model_a_id", "rating_events", ["model_a_id"])
    op.create_index("ix_rating_events_model_b_id", "rating_events", ["model_b_id"])
    op.create_index("ix_rating_events_created_at", "rating_events", ["created_at"])


def downgrade() -> None:
    op.drop_table("rating_events")
    op.drop_table("model_ratings")
    op.drop_table("votes")
    op.drop_table("runs")
    op.drop_table("battles")
    op.drop_table("tasks")
    op.drop_table("task_sets")
    op.drop_table("user_profiles")
    op.drop_table("users")
    op.drop_table("models")
    op.drop_table("prompt_templates")
