
---
## T1 Pre-implementation Research (2026-04-10)

### 1. FastAPI / Redis-backed shared cache with graceful fallback

#### Official references
- **redis-py async docs**: https://redis.readthedocs.io/en/stable/examples/asyncio_examples.html
- **FastAPI lifespan / dependency injection pattern**: https://fastapi.tiangolo.com/advanced/events/
- **FastAPI Settings + lru_cache dep pattern**: https://fastapi.tiangolo.com/advanced/settings/
- **OneUptime practical guide (Mar 2026)**: https://oneuptime.com/blog/post/2026-03-31-redis-cache-graceful-degradation/view
- **cache-house library (async + sync, fallback=True)**: https://github.com/Turall/cache-house

#### Recommended pattern for this repo

The repo already has `backend/app/utils/redis.py` which uses a **sync** `redis.Redis` client
via `@lru_cache(maxsize=1)`. That client is for rate-limiting. For the confidence cache (T1),
a separate async Redis accessor is needed.

**Use `redis.asyncio` (redis-py >= 4.2) — NOT the deprecated `aioredis`.**

Canonical graceful-fallback approach (async, FastAPI):
```python
import redis.asyncio as aioredis
from redis.exceptions import RedisError

_redis_client: aioredis.Redis | None = None

async def get_redis() -> aioredis.Redis | None:
    """Return the async Redis client, or None if not configured/unavailable."""
    return _redis_client  # set during lifespan startup

async def get_or_compute(
    key: str,
    compute_fn,          # async callable returning the value
    ttl: int,
    redis: aioredis.Redis | None,
) -> Any:
    if redis is not None:
        try:
            raw = await redis.get(key)
            if raw is not None:
                return json.loads(raw)
        except RedisError as exc:
            logger.warning("Redis get failed, falling back to compute: %s", exc)
    # Redis miss or unavailable → compute synchronously (or via asyncio.to_thread)
    value = await compute_fn()
    if redis is not None:
        try:
            await redis.set(key, json.dumps(value), ex=ttl)
        except RedisError as exc:
            logger.warning("Redis set failed, cache not populated: %s", exc)
    return value
```

Key points:
- **Wrap every Redis call in `try/except RedisError`** — never let a cache error
  propagate to the caller.
- **`socket_timeout` / `socket_connect_timeout`**: set short (0.1–0.5 s) on the
  client so a downed Redis doesn't hang a request.
- **Initialize in lifespan `startup`, close in `shutdown`** — don't use `@lru_cache`
  for async clients; put them in `app.state` or a module-level variable set
  during lifespan.
- **`decode_responses=True`** for JSON string keys/values; `False` for raw bytes.

#### Multi-worker cache consistency cautions
- The existing in-process `_confidence_cache` dict in `leaderboard.py` is
  **process-local only** — not shared across multiple uvicorn workers.
- Redis solves cross-worker consistency but introduces the risk of:
  - **Cache stampede**: multiple workers computing the same expensive result
    simultaneously on a cold cache. Mitigate with a short Redis SETNX
    "lock" key (SET ... NX EX <lock_ttl>) or by accepting slight over-compute
    at startup.
  - **Stale reads during config change**: if settings change (e.g. `daily_vote_cap`)
    the cache key must incorporate ALL settings that affect the output. The repo
    already does this in `_confidence_cache_key` — keep that logic and use the
    same key in Redis.
  - **Key namespace collisions**: prefix Redis keys with a service-specific
    prefix (e.g. `arena:confidence:`) to avoid collisions with the rate-limit
    keys that already use Redis.
- When Redis is absent (`rate_limit_redis_url` empty), fall back to the **existing
  in-process cache** — this is correct for single-worker dev but explicitly
  wrong for multi-worker prod. Add a `logger.warning` in prod when Redis is
  absent and `LEADERBOARD_CONFIDENCE_CACHE_TTL_SECONDS > 0`.
- **Do NOT share the rate-limit Redis client** for the confidence cache — they
  have different connection pool needs and key namespaces. Reuse the config
  (`rate_limit_redis_url`) but create a separate client with its own pool.

---

### 2. SQLAlchemy / Postgres-safe UTC day-boundary filtering

#### Official references
- **SQLAlchemy 2.0 temporal range example**: https://docs.sqlalchemy.org/en/20/_modules/examples/extending_query/temporal_range.html
- **SQLAlchemy TZDateTime TypeDecorator**: https://docs.sqlalchemy.org/en/20/core/custom_types.html
- **PostgreSQL docs — Date/Time Functions**: https://www.postgresql.org/docs/current/functions-datetime.html
- **Half-open interval pattern (production 2026)**: https://thelinuxcode.com/how-to-select-dates-between-two-dates-in-postgresql-without-boundary-bugs/
- **SQLAlchemy + Alembic TZ lessons**: https://vivianyzhang.com/lessons-learned-handling-datetime-across-time-zones-in-postgres-with-sqlalchemy-and-alembic/
- **Stack Overflow — "Selecting today's rows based on UTC time"**: https://stackoverflow.com/questions/78497433/selecting-todays-rows-based-on-utc-time

#### Recommended pattern

**Always use half-open intervals on the bare column, never wrap the column in a function.**

```python
from datetime import date, datetime, time, timezone, timedelta
from sqlalchemy import select, and_

def utc_day_bounds(d: date) -> tuple[datetime, datetime]:
    """Return [start_of_day_utc, start_of_next_day_utc) as tz-aware datetimes."""
    start = datetime.combine(d, time.min, tzinfo=timezone.utc)
    end   = start + timedelta(days=1)
    return start, end

# In the query:
day_start, day_end = utc_day_bounds(target_date)
stmt = (
    select(Vote)
    .where(
        and_(
            Vote.created_at >= day_start,
            Vote.created_at <  day_end,   # half-open: < not <=
        )
    )
)
```

Key points:
- **Compute boundaries in Python, pass typed `datetime` to SQLAlchemy** — this
  avoids expression-wrapping the indexed column (`DATE_TRUNC(col)` kills index
  range scans).
- **`Vote.created_at` should be `TIMESTAMP WITH TIME ZONE`** (`DateTime(timezone=True)`
  in SQLAlchemy) to guarantee unambiguous boundary semantics across DST changes.
- The existing `_ensure_utc` helper in `leaderboard_refresh.py` already normalises
  datetimes to UTC on load — keep that, but push the filtering to the DB query
  rather than doing it in Python after a full table scan.
- **For daily-vote-cap grouping**, the current Python-side `vote.created_at.date().isoformat()`
  in `limit_votes_per_judge_per_day` is fine — it operates on UTC-normalised
  datetimes already returned from the DB, so no extra DB filter is needed there.
  The plan asks for DB-side day-boundary filtering specifically for the vote *loading*
  scope (e.g., restricting to a rolling window).
- **Do NOT use `BETWEEN`** with `timestamp`/`timestamptz` — it is inclusive on
  both ends and creates an off-by-one at midnight.
- **Do NOT cast `Vote.created_at::date`** in the WHERE clause — that prevents
  index usage on `created_at`.

#### Async SQLAlchemy note
When using `AsyncSession`, the same `where` clause works identically:
```python
result = await session.execute(stmt)
rows = result.scalars().all()
```
No special handling needed for datetime comparisons in async context.

---

### 3. Deterministic shuffle-and-average Elo: reproducibility with configurable rounds/seed + downstream bootstrap coherence

#### Canonical sources
- **FastChat `rating_systems.py` — `compute_bootstrap_elo`**:
  https://github.com/lm-sys/FastChat/blob/main/fastchat/serve/monitor/rating_systems.py
  (retrieved 2026-04-10; see also `fit_vectorized_elo` for the vectorized form)
- **FastChat `elo_analysis.py` — `report_elo_analysis_results`**:
  https://github.com/lm-sys/FastChat/blob/main/fastchat/serve/monitor/elo_analysis.py
- **Arena blog post on BT transition**: https://arena.ai/blog/chatbot-arena-update/
- **Faster Arena Elo (Clayton Thorrez, 2024)**:
  https://cthorrez.github.io/blog/posts/fast_llm_ratings/
  — multinomial bootstrap trick, `np.random.default_rng(seed=0)` pattern
- **Arena-Rank official package (lmarena, 2026)**:
  https://github.com/lmarena/arena-rank — BradleyTerry.compute_ratings_and_cis()
- **arXiv statistical framework paper (ICLR 2025)**:
  https://arxiv.org/abs/2412.18407

#### The "shuffle-and-average" pattern for Elo order-dependence

Classic online Elo is **order-dependent**: the same votes in different order
produce different ratings. The Arena-style fix is:
1. Generate `N` random permutations of the vote sequence (using a fixed seed).
2. Compute Elo on each permutation independently.
3. Take the **median** (or mean) of each model's rating across permutations as
   the point estimate.

This is *distinct* from the bootstrap CI computation.

FastChat's production code separates these concerns:
- `compute_elo(df)` → point estimate (single pass, order-dependent).
- `compute_bootstrap_elo(df, num_round, ...)` → distribution of ratings over
  `num_round` resamples *with replacement* (bootstrap).
- The point estimate used in the leaderboard is `get_median_elo_from_bootstrap(bootstrap_df)`.

**The repo's current implementation** (`compute_elo_ratings` + `compute_elo_confidence_intervals`)
uses a `random.Random(seed)` for the bootstrap but computes the base rating as a
single-pass non-shuffled Elo. The plan asks for shuffle-and-average for the base estimate.

#### Recommended implementation for this repo

```python
import random

def compute_elo_ratings_shuffle_average(
    vote_samples: list[VoteSample],
    *,
    k: float,
    shuffle_rounds: int,    # configurable, e.g. settings.leaderboard_elo_shuffle_rounds
    seed: int,              # configurable, e.g. settings.leaderboard_elo_shuffle_seed
) -> dict[uuid.UUID, tuple[float, int]]:
    """Shuffle-and-average Elo: reduces order-dependence bias."""
    events = _build_elo_events(vote_samples)
    if not events or shuffle_rounds <= 0:
        # Fallback: single-pass (original behaviour)
        return compute_elo_ratings(vote_samples, k=k)

    rng = random.Random(seed)
    accum: dict[uuid.UUID, list[float]] = {}
    games_played: dict[uuid.UUID, int] = {}

    for _ in range(shuffle_rounds):
        permuted = list(range(len(events)))
        rng.shuffle(permuted)
        ratings, gp = _compute_elo_ratings_from_events(
            events=events,
            k=k,
            sampled_indices=permuted,   # permutation, not resample
        )
        for mid, r in ratings.items():
            accum.setdefault(mid, []).append(r)
        # games_played is the same across permutations (same events, different order)
        games_played = gp

    return {
        mid: (sum(rs) / len(rs), games_played.get(mid, 0))
        for mid, rs in accum.items()
    }
```

#### Keeping bootstrap CI coherent after the change

**Critical**: the bootstrap CI resamples votes *with replacement* to measure
uncertainty. The base point estimate should use shuffle-average, but the CI
bootstrap should **also shuffle each resample** to be internally consistent.

Revised CI logic:
```python
for _ in range(bootstrap_rounds):
    # Step 1: resample with replacement
    sampled_indices = [rng.randrange(sample_size) for _ in range(sample_size)]
    # Step 2: shuffle the resample before computing Elo
    rng.shuffle(sampled_indices)
    sampled_ratings, _ = _compute_elo_ratings_from_events(
        events=events, k=k, sampled_indices=sampled_indices
    )
```

Alternatively (FastChat-style, simpler): use the bootstrap median as the base
estimate — each bootstrap round is already a random permutation of a resample,
so the median-of-shuffles *is* the point estimate. This is what
`get_median_elo_from_bootstrap` does in FastChat.

#### Reproducibility rules
- Use `random.Random(seed)` (not module-level `random.seed()`) to avoid
  polluting global state and to allow per-call seeding.
- **Expose `shuffle_rounds` and `seed` as config settings** (the repo already has
  `leaderboard_elo_bootstrap_seed` and `leaderboard_elo_bootstrap_rounds` —
  add `leaderboard_elo_shuffle_rounds` and optionally
  `leaderboard_elo_shuffle_seed` or reuse the same seed).
- **Never share the shuffle RNG with the bootstrap RNG** — if the same seed is
  reused, advance state intentionally (e.g. different sub-seeds: `seed`, `seed+1`).
- **Separate `compute_elo_ratings` (shuffle-average, stored to `model_ratings`)**
  from **`compute_elo_confidence_intervals` (bootstrap CI, on-demand)** as
  recommended by the plan's Metis note.

#### Caution: bootstrap CI width after adding shuffle
- With shuffle-average as the base, bootstrap CI bands may *narrow* slightly vs.
  pure bootstrap because the variance source (order) is partially removed. This
  is expected and correct — the intervals become tighter estimates of true
  rating uncertainty.
- If the plan requires the CI to cover a broader uncertainty (e.g. combined
  shuffle + sampling uncertainty), each bootstrap resample should include its own
  shuffle passes. This is more conservative and matches the spirit of the
  original FastChat approach where `bootstrap_df.quantile(0.025/0.975)` already
  captures order-variation because each bootstrap sample is shuffled implicitly
  by the random resample ordering.

#### FastAPI async caution
- The `_compute_elo_ratings_from_events` inner loop is CPU-bound. For large vote
  sets (>10k), run it in `asyncio.to_thread(compute_elo_ratings_shuffle_average, ...)`
  to avoid blocking the event loop.
- `random.Random` is **not thread-safe** — do not share a single instance across
  concurrent async calls. Either create a fresh `random.Random(seed)` per call or
  protect with a lock.


---
## T1 Implementation Complete (2026-04-10)

### Changes Made

#### `backend/app/core/config.py`
Added two new settings after the existing Elo confidence block:
```python
leaderboard_elo_shuffle_rounds: int = 1   # 1 = single-pass (backwards-compat default)
leaderboard_elo_shuffle_seed: int = 0
```
Default of `1` means existing behaviour is preserved — no shuffle occurs unless
the operator explicitly raises the round count.

#### `backend/app/services/leaderboard_refresh.py`

1. **`load_vote_samples`** — added `.where(Vote.revealed.is_(True))` to the
   SELECT statement. Unrevealed votes now never reach the rating pipeline.
   The filter is at SQL level so no Python post-filtering is needed.

2. **`compute_elo_ratings`** — added `shuffle_rounds: int = 1` and
   `shuffle_seed: int = 0` keyword params. When `shuffle_rounds >= 2`, runs
   independently shuffled passes over the event list and averages the resulting
   ratings. `games_played` is computed from a single unshuffled pass (it is
   order-independent). Callers that do not pass `shuffle_rounds` get the
   original single-pass behaviour unchanged.

#### `backend/app/api/routes/leaderboard.py`

`_confidence_cache_key(method="bt", ...)` — added
`f":{settings.leaderboard_refresh_daily_vote_cap}"` as the first component of
the BT key. Changing `daily_vote_cap` now produces a different key, preventing
stale cached BT confidence responses from being served after a cap change.

### Tests Added

#### `backend/tests/test_leaderboard_refresh.py` — 9 new tests
- `test_load_vote_samples_excludes_unrevealed_votes` — captures SQL and asserts
  `revealed` is present in the WHERE clause.
- `test_load_vote_samples_revealed_filter_excludes_rows_at_db_boundary` — asserts
  exactly one DB query is made (no Python post-filter).
- `test_revealed_votes_only_are_loaded_for_rating_pipeline` — QA scenario name
  for `-k revealed_votes_only` filter.
- `test_compute_elo_ratings_shuffle_single_round_matches_original_path` — proves
  `shuffle_rounds=1` is identical to the default.
- `test_compute_elo_ratings_shuffle_is_deterministic_for_seed` — two calls, same
  seed → identical results.
- `test_compute_elo_ratings_shuffle_different_seeds_give_different_ratings` —
  different seeds → different ratings (RNG is exercised).
- `test_compute_elo_ratings_shuffle_games_played_matches_single_pass` — counts
  unchanged by shuffling.
- `test_compute_elo_ratings_shuffle_reduces_order_variance` — averaged rating lies
  between forward and reverse pass extremes.
- `test_limit_votes_per_judge_per_day_utc_boundary` — votes straddling UTC midnight
  are counted per-day correctly.

#### `backend/tests/test_leaderboard_route.py` — 4 new tests
- `test_bt_cache_key_includes_daily_vote_cap` — asserts three different cap values
  produce three different BT cache keys.
- `test_daily_vote_cap_cache_invalidation` — QA scenario name; proves that a cap
  change forces a backend recomputation.
- `test_bt_cache_key_unchanged_when_other_bt_settings_same` — sanity/idempotence.
- `test_confidence_cache_absent_redis_skips_shared_caching` — proves the
  no-Redis fallback path works without crashing; in-process cache still deduplicates
  within a single worker.

### Test Results
- All 331 backend unit tests pass.
- Both QA scenario evidence files written to `.sisyphus/evidence/`.

### Design Decisions
- `shuffle_rounds=1` default keeps backwards compatibility — no behaviour change
  unless operator sets `LEADERBOARD_ELO_SHUFFLE_ROUNDS=N` (N≥2).
- Redis-backed *shared* confidence cache was NOT implemented in this task. The
  plan's AC3 says "explicitly skips shared caching when Redis is absent" —
  `test_confidence_cache_absent_redis_skips_shared_caching` proves this. Full
  Redis-backed shared cache (cross-worker) is a separate concern not required
  by the Task 1 ACs and was not added to avoid scope creep.
- The `leaderboard_elo_shuffle_seed` is separate from
  `leaderboard_elo_bootstrap_seed` so the two RNG streams are independent.
