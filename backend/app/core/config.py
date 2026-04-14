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

    # Logging / observability.
    log_level: str = ""
    log_json: bool = False
    access_log_enabled: bool = False

    database_url: str = "postgresql+psycopg://postgres:postgres@localhost:5432/arena"

    # Accept CSV env values ("http://a,http://b") for local ergonomics.
    cors_allow_origins: Annotated[list[str], NoDecode] = ["http://localhost:3000"]

    oidc_issuer: str = ""
    oidc_audience: str | None = None
    oidc_admin_group_claim: str = "groups"
    oidc_admin_group_name: str = "arena_admin"
    oidc_jwks_cache_ttl_seconds: int = 300
    oidc_http_timeout_seconds: float = 5.0

    # SECURITY: Empty salt defaults are insecure. Set these to random values
    # in production to prevent rainbow-table attacks on hashed identifiers.
    anon_ip_hash_salt: str = ""
    anon_user_agent_hash_salt: str = ""

    turnstile_secret_key: str = ""
    turnstile_verify_url: str = (
        "https://challenges.cloudflare.com/turnstile/v0/siteverify"
    )

    # Anonymous anti-abuse throttles.
    # Set limit <= 0 to disable that limiter.
    anon_rate_limit_bucket_seconds: int = 10
    anon_battle_create_rate_limit: int = 20
    anon_battle_create_rate_limit_window_seconds: int = 60
    anon_battle_stream_rate_limit: int = 30
    anon_battle_stream_rate_limit_window_seconds: int = 60
    anon_vote_submit_rate_limit: int = 30
    anon_vote_submit_rate_limit_window_seconds: int = 60

    # Authenticated user rate limits (higher thresholds than anonymous).
    # Set limit <= 0 to disable that limiter.
    auth_battle_create_rate_limit: int = 60
    auth_battle_create_rate_limit_window_seconds: int = 60
    auth_battle_stream_rate_limit: int = 90
    auth_battle_stream_rate_limit_window_seconds: int = 60
    auth_vote_submit_rate_limit: int = 90
    auth_vote_submit_rate_limit_window_seconds: int = 60
    # Default True so cookies are only sent over HTTPS in production.
    # Set to False explicitly for local HTTP development.
    anon_id_cookie_secure: bool = True

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
    arena_master_key_old: str = ""

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

    # Leaderboard refresh background job.
    leaderboard_refresh_enabled: bool = True
    leaderboard_refresh_interval_seconds: int = 300
    leaderboard_refresh_daily_vote_cap: int = 0
    leaderboard_refresh_elo_k: float = 32.0

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

        if not self.anon_ip_hash_salt:
            errors.append(
                "ANON_IP_HASH_SALT is empty in production — "
                "IP hashes are trivially reversible without a salt"
            )
        if not self.anon_user_agent_hash_salt:
            errors.append(
                "ANON_USER_AGENT_HASH_SALT is empty in production — "
                "user-agent hashes are insecure without a salt"
            )
        if not self.oidc_issuer:
            errors.append(
                "OIDC_ISSUER is empty in production — "
                "OIDC authentication is disabled; all requests will be "
                "treated as anonymous"
            )
        if self.oidc_issuer and not self.oidc_audience:
            errors.append(
                "OIDC_AUDIENCE is empty while OIDC_ISSUER is set — "
                "audience validation is disabled, tokens for any audience "
                "on this issuer will be accepted"
            )
        if not self.arena_master_key:
            errors.append(
                "ARENA_MASTER_KEY is empty — API key encryption at rest is disabled"
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

        if not self.anon_id_cookie_secure:
            errors.append(
                "ANON_ID_COOKIE_SECURE is False in production — "
                "the anonymous identity cookie will be sent over plain HTTP, "
                "allowing session hijacking via network interception"
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
                "anonymous rate limiting and shared confidence caching are "
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
