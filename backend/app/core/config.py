"""app.core.config

Application configuration via environment variables.

Notes:
- Keep settings typed and explicit.
- Secrets should come from environment/Kubernetes secrets, not from git.
"""

from __future__ import annotations

from typing import Annotated, Any
from functools import lru_cache
import json
import logging

from pydantic import Field, field_validator, model_validator
from pydantic_settings import (  # pyright: ignore[reportMissingImports]
    BaseSettings,
    NoDecode,
    SettingsConfigDict,
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "dev"
    app_name: str = "OpenSakura Arena API"
    api_v1_prefix: str = "/api/v1"
    public_base_url: str = ""

    # Logging / observability.
    log_level: str = ""
    log_json: bool = False
    access_log_enabled: bool = False

    # OTLP tracing is a no-op until an endpoint is configured by a later task.
    otlp_disabled: bool = False
    otlp_endpoint: str = ""
    otel_exporter_otlp_traces_endpoint: str = ""
    otel_exporter_otlp_endpoint: str = ""
    otlp_auth_header: str = ""
    otlp_project_name: str = ""
    otel_service_name: str = "opensakura-arena-backend"
    otel_resource_attributes: str = ""
    otlp_exporter_timeout_seconds: float = 30.0
    otlp_batch_export_timeout_millis: int = 10000
    openinference_hide_inputs: bool = True
    openinference_hide_outputs: bool = True
    openinference_hide_input_messages: bool = True
    openinference_hide_output_messages: bool = True
    openinference_hide_input_text: bool = True
    openinference_hide_output_text: bool = True
    openinference_hide_llm_invocation_parameters: bool = True
    otel_instrumentation_genai_capture_message_content: bool = False

    database_url: str = "postgresql+psycopg://postgres:postgres@localhost:5432/arena"

    # Accept CSV env values ("http://a,http://b") for local ergonomics.
    cors_allow_origins: Annotated[list[str], NoDecode] = ["http://localhost:3000"]

    oidc_issuer: str = ""
    oidc_admin_group_claim: str = "groups"
    oidc_admin_group_name: str = "arena_admin"
    oidc_jwks_cache_ttl_seconds: int = 300
    oidc_http_timeout_seconds: float = 5.0
    oidc_client_id: str = ""
    oidc_client_secret: str = ""
    oidc_client_auth_method: str = "client_secret_basic"
    oidc_scope: str = "openid email profile"
    oidc_redirect_path: str = "/api/v1/auth/callback"
    oidc_login_state_max_age_seconds: int = 600

    auth_session_cookie_name: str = "arena_session"
    auth_login_state_cookie_name: str = "arena_oauth_state"
    auth_session_max_age_seconds: int = 28800
    auth_session_hash_secret: str = ""
    auth_session_last_seen_min_interval_seconds: int = 60
    auth_session_last_seen_lock_timeout_ms: int = 100
    auth_session_last_seen_statement_timeout_ms: int = 500
    auth_csrf_header_name: str = "X-CSRF-Token"
    auth_cookie_secure: bool | None = None

    database_idle_in_transaction_session_timeout_ms: int = 30000

    turnstile_secret_key: str = ""
    turnstile_verify_url: str = (
        "https://challenges.cloudflare.com/turnstile/v0/siteverify"
    )

    anon_rate_limit_bucket_seconds: int = 10

    # Authenticated user rate limits.
    # Set limit <= 0 to disable that limiter.
    auth_battle_create_rate_limit: int = 60
    auth_battle_create_rate_limit_window_seconds: int = 60
    auth_battle_stream_rate_limit: int = 90
    auth_battle_stream_rate_limit_window_seconds: int = 60
    auth_vote_submit_rate_limit: int = 90
    auth_vote_submit_rate_limit_window_seconds: int = 60

    # Redis backend for rate limiting (anonymous and authenticated) and shared
    # confidence-interval result caching. Leave empty to disable both.
    rate_limit_redis_url: str = ""
    rate_limit_redis_key_prefix: str = "arena"
    rate_limit_redis_timeout_seconds: float = 0.25

    # Trust the X-Forwarded-For header when extracting client IPs for
    # rate limiting.  **Must only be enabled when the app runs behind a
    # trusted reverse proxy** (e.g. Nginx, Caddy, Cloud LB) that
    # overwrites this header.  When False (default), the direct
    # ``request.client.host`` is used, which is safe but will be the
    # proxy's IP if one is present.
    trust_x_forwarded_for: bool = False

    arena_master_key: str = ""
    # Optional previous master key for zero-downtime key rotation.
    # When set, decryption will attempt the current key first, then fall back
    # to this key.  Leave empty when no rotation is in progress.
    arena_master_key_old: str = ""

    service_token_hash_secret: str = ""

    # LLM client and outbound OpenAI-compatible request controls.
    openai_connect_timeout_seconds: float = 10.0
    openai_model_timeout_seconds: float = 120.0
    max_concurrent_llm_requests: int = 40
    max_llm_requests: int = 120
    llm_queue_wait_timeout_seconds: float = 30.0
    llm_queue_shutdown_timeout_seconds: float = 10.0
    admin_model_test_timeout_seconds: float = 20.0

    # FastChat-inspired model pairing controls (model_name keyed).
    battle_sampling_weights: dict[str, float] = Field(default_factory=dict)
    battle_targets: dict[str, list[str]] = Field(default_factory=dict)
    battle_strict_targets: dict[str, list[str]] = Field(default_factory=dict)
    battle_outage_models: list[str] = Field(default_factory=list)
    battle_sampling_boost_models: list[str] = Field(default_factory=list)

    # Worker count guard: battle execution relies on in-process singletons
    # (orchestrator, leaderboard refresher) that are NOT shared across OS
    # processes.  Production deployments MUST run exactly one uvicorn worker.
    # Set WEB_CONCURRENCY=1 in your process manager / container spec.
    # Values <= 0 are treated as 1 (unset / not applicable).
    web_concurrency: int = 1

    # Maximum wall-clock seconds the owned battle task may run before being
    # force-failed.  Applies to active battle execution by this process's
    # BattleOrchestrator, not to cross-process observer polling (which is
    # only a fallback for stale "running" battles left by a prior process).
    battle_running_wait_timeout_seconds: int = 600

    # Battle prepopulation pool controls.
    battle_prepopulation_max_job_size: int = 50
    battle_prepopulation_job_timeout_seconds: int = 900
    battle_prepopulation_enabled: bool = True
    battle_pool_user_recycle_after_hours: int = 24
    battle_pool_assignment_ttl_seconds: int = 900

    # Leaderboard refresh background job.
    leaderboard_refresh_enabled: bool = True
    leaderboard_refresh_interval_seconds: int = 300
    leaderboard_refresh_daily_vote_cap: int = 0
    leaderboard_refresh_elo_k: float = 32.0

    # Optional FastChat-inspired judge outlier filtering for rating inputs.
    # Disabled by default to preserve existing leaderboard behavior.
    leaderboard_outlier_filter_enabled: bool = False
    leaderboard_outlier_min_votes: int = 5
    leaderboard_outlier_max_votes: int = 100
    leaderboard_outlier_alpha: float = 0.05

    # Confidence-enabled leaderboard requests are CPU-expensive (bootstrap).
    # Cache short-term and optionally throttle uncached recomputations.
    leaderboard_confidence_cache_ttl_seconds: int = 15
    leaderboard_confidence_rate_limit: int = 6
    leaderboard_confidence_rate_limit_window_seconds: int = 60

    # Elo confidence interval settings (computed on demand).
    leaderboard_elo_bootstrap_rounds: int = 200
    leaderboard_elo_bootstrap_seed: int = 0
    leaderboard_elo_confidence_level: float = 0.95

    # Shuffle-and-average Elo: run this many shuffled passes before computing
    # the base rating to reduce order-dependent artifacts. Set to 0 or 1 to
    # use the original single-pass path.
    leaderboard_elo_shuffle_rounds: int = 5
    leaderboard_elo_shuffle_seed: int = 0

    # Bradley-Terry leaderboard settings (computed on demand).
    leaderboard_bt_max_iterations: int = 200
    leaderboard_bt_tolerance: float = 1e-6
    leaderboard_bt_bootstrap_rounds: int = 200
    leaderboard_bt_bootstrap_seed: int = 0
    leaderboard_bt_prior: float = 1e-6
    leaderboard_bt_confidence_level: float = 0.95

    @model_validator(mode="after")
    def _validate_production_settings(self) -> "Settings":
        """Raise when security-critical settings are left at their defaults in
        production.  This prevents misconfigured deployments from running in an
        insecure state."""
        _NON_PRODUCTION_ENVS = {"dev", "development", "test", "testing", "local"}
        if self.app_env.lower() in _NON_PRODUCTION_ENVS:
            return self

        errors: list[str] = []

        if not self.oidc_issuer.strip():
            errors.append(
                "OIDC_ISSUER is empty in production — "
                "OIDC authentication is disabled; all requests will be "
                "treated as unauthenticated"
            )
        if not self.oidc_client_id.strip():
            errors.append(
                "OIDC_CLIENT_ID is empty in production — "
                "the backend cannot perform confidential-client OIDC login"
            )
        if not self.oidc_client_secret.strip():
            errors.append(
                "OIDC_CLIENT_SECRET is empty in production — "
                "the backend cannot authenticate as a confidential OIDC client"
            )
        if not self.auth_session_hash_secret.strip():
            errors.append(
                "AUTH_SESSION_HASH_SECRET is empty in production — "
                "auth session lookup hashing is disabled"
            )
        if not self.public_base_url.strip():
            errors.append(
                "PUBLIC_BASE_URL is empty in production — "
                "backend OIDC redirect URI construction is disabled"
            )
        if not self.arena_master_key:
            errors.append(
                "ARENA_MASTER_KEY is empty — API key encryption at rest is disabled"
            )
        if not self.service_token_hash_secret.strip():
            errors.append(
                "SERVICE_TOKEN_HASH_SECRET is empty — "
                "service token HMAC hashing is disabled"
            )
        if (
            self.database_url
            == "postgresql+psycopg://postgres:postgres@localhost:5432/arena"
        ):
            errors.append(
                "DATABASE_URL is still set to the default localhost/postgres — "
                "set it to the production database connection string"
            )
        effective_workers = self.web_concurrency if self.web_concurrency > 0 else 1
        if effective_workers > 1:
            errors.append(
                f"WEB_CONCURRENCY is {self.web_concurrency} — "
                "battle execution relies on in-process singletons and is only "
                "safe with a single API worker. Set WEB_CONCURRENCY=1."
            )

        # Reject wildcard CORS origins when credentials are enabled.
        if any(origin.strip() == "*" for origin in self.cors_allow_origins):
            errors.append(
                "CORS_ALLOW_ORIGINS contains '*' — "
                "wildcard origins with allow_credentials=True are insecure; "
                "set explicit origin(s)"
            )

        if errors:
            raise ValueError(
                "Security-critical production configuration errors:\n"
                + "\n".join(f"  - {e}" for e in errors)
            )

        _config_logger = logging.getLogger("app.core.config")

        if not self.rate_limit_redis_url.strip():
            _config_logger.warning(
                "RATE_LIMIT_REDIS_URL is not configured in production — "
                "Redis-backed rate limiting and shared confidence caching are "
                "disabled"
            )

        return self

    @field_validator("cors_allow_origins", mode="before")
    @classmethod
    def _parse_cors_allow_origins(cls, value: Any) -> list[str]:
        if isinstance(value, str):
            raw = value.strip()
            if not raw:
                return []

            # Keep compatibility with explicit JSON arrays when desired.
            if raw.startswith("["):
                try:
                    value = json.loads(raw)
                except ValueError:
                    # Malformed JSON array — strip exactly the surrounding
                    # brackets so the subsequent CSV split does not embed
                    # them into the first/last origin strings.
                    value = raw[1:-1] if len(raw) >= 2 else raw
            else:
                value = raw

        if isinstance(value, (list, tuple, set)):
            origins: list[str] = []
            for item in value:
                if not isinstance(item, str):
                    raise ValueError("CORS origins must be strings")
                origin = item.strip()
                if origin:
                    origins.append(origin)
            return origins

        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]

        raise ValueError("Invalid CORS origin configuration")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
