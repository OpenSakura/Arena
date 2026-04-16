<!--
docs/arena-alignment.md

Alignment notes captured from an initial Q&A.

Notes:
- This document exists to keep implementation choices explicit and reproducible.
- Treat this as a living spec; update it when decisions change.
-->

# OpenSakura Arena - Alignment Notes (Living Spec)

## Purpose

- Keep product/engineering decisions explicit.
- Make reproducibility requirements concrete (what we must persist).
- Record security constraints and known risks early.

## Product Goal

Build a community translation arena for JP->ZH (light novels / galgame style):

- Pairwise "battle" comparisons between two model translations (blind A/B).
- Human voting with optional rubric tags.
- Reproducible run logs so arena data can become datasets.

Non-goals for MVP (explicit):

- No internal model serving stack in this repo.
- No worker queue or async job system (API calls only).
- No ClickHouse/analytics stack (Postgres only).
- No BYOK (bring-your-own-key) mode.
- No user-paste content mode (curated public tasks only).

## Glossary (Working Terms)

- Task: A single JP source passage to translate.
- TaskSet: A curated collection of tasks used for sampling and filtering.
- Model: A callable entry in the model registry.
- PromptConfig: A per-model prompt configuration (system/user prompt text, plain-text, not DB-versioned) used to build request messages.
- Run: One model execution on one task (request + output + stats).
- Battle: One A/B comparison built from two runs on the same task.
- Vote: A judgment selecting A, B, or tie (+ rubric tags + optional comment).

Important nuance:

- If each model uses its own prompt configuration, leaderboards are comparing
  (model + prompt_config + params) as a system. This is OK, but must be explicit in
  exports and leaderboard filtering.

## Stack + Scope (MVP)

- Frontend: React + Vite SPA (TypeScript), single app for arena + admin.
- Backend: FastAPI (Python), Postgres only.
- Auth: Authentik OIDC. Login required for all mutations (battle creation,
  voting, retries, reveal). Public read pages remain accessible without login.
- No worker queue: model calls are performed directly by the API (async).
- No gateway service in this repo: an upstream gateway already exists.

Best assumptions for implementation:

- Upstream gateway exposes an OpenAI-compatible API surface (chat-style requests).
- Streaming is supported (or can be disabled per model if needed).
- Usage/tokens and request ids may be returned by the gateway; we persist when present.

## Confirmed Decisions (Q&A)

### Model Calling Convention

- All models are called through an OpenAI-compatible interface ("OpenAI client").
- Requests are handled upstream by an existing gateway.
- Each model entry stores `base_url` + `model_name` + (optional) encrypted API key.

Notes / assumptions:

- The backend uses one OpenAI-compatible client adapter for every model; no per-provider SDKs.
- In practice, `base_url` is expected to be an upstream gateway instance.
- `base_url` is allowed to be arbitrary and may be internal; treat it as sensitive and admin-only.
- `base_url` may be configured either as `https://gateway.example.com` or `https://gateway.example.com/v1`.
  The backend normalizes this to avoid accidental `/v1/v1/...` paths.

### Model Registry Fields

Per-model config must support:

- `temperature`
- `frequency_penalty`
- `presence_penalty`
- `extra_body` (JSON forwarded upstream; gateway-specific extensions)

Additional parameters remain supported via `default_params` (JSON).

Notes:

- `default_params` covers the rest of the OpenAI-compatible surface (top_p, max_tokens, stop,
  seed, response_format, etc.).
- `extra_body` is forwarded upstream and may enable gateway-specific routing/features.
- For reproducibility, each Run should store a config snapshot/hash at execution time (model
  settings can change later).

### Keys / Billing

- Platform keys: the backend stores encrypted per-model keys (or shared keys).
- BYOK is not part of MVP.

### Prompt Standardization

- Per-model prompt configuration is allowed (each model can use its own system/user prompt).
- Battles are still A/B blind; model identities are hidden until after vote.

Notes:

- This means the arena measures system performance, not just the raw base model.
- Plan to slice leaderboards/exports by prompt config so results stay interpretable.

### Battle Pairing

- Pairing uses weighted sampling inspired by FastChat (exact implementation TBD).
- You plan to provide the FastChat reference code for the sampling strategy.

### Task Source + Storage

- MVP battles use curated public passages only.
- Task text used in a battle is snapshotted in Postgres for reproducibility.
- Longer-term: you will build a data collector from your own text DB; arena does not need to
  solve that now.

Notes:

- "Snapshot in Postgres" means store the exact JP text used in each Task/Battle for self-contained
  exports even if upstream sources change.
- Keep task metadata for provenance (work title, chapter, license, source url, tags).

### Output Policy

- Model output must be translation only (no reasoning / analysis / chain-of-thought).

Notes:

- Prompts should explicitly require "translation only" output.
- If models still emit headings/notes, keep raw output (transparency) and optionally add a
  normalized field later (store both).

### Voting

- Voting requires authentication.
- Vote data: A/B/tie + rubric tags + optional comment.
- Reveal: show model identities only after the vote is submitted.

### User Accounts and Profiles

- Authentik OIDC is the identity provider.
- Login is required to create battles, submit votes, retry battles, and reveal
  votes. Public read pages (battles, leaderboards, completed results) remain
  accessible without login.
- Logged-in users may optionally fill a profile for downstream filtering/analysis:
  - JP capability (e.g., JLPT N1-N5 or other certificates)
  - translation experience per language pair and role (translator/editor/qc/tl/etc.)
- Votes always store `voter_user_id` from the authenticated session.

### Rate Limits / Anti-abuse

- Authenticated users: no rate limit by default (acknowledged cost/abuse risk).
- Cloudflare Turnstile can be enabled as an optional extra verification layer
  on top of authentication.

Notes / best assumptions:

- "Unlimited" by policy for authenticated users, but we should still have operational
  backstops: budget alerts, anomaly detection, and the ability to throttle/ban
  users manually.

### Logging / Reproducibility

- Persist full run details:
  - rendered prompt/messages
  - parameters
  - model identifiers
  - timings/latency
  - token usage returned by upstream
  - output text

Additional recommended fields:

- upstream request id / trace id (if provided)
- full upstream request payload snapshot (messages + params + extra_body)
- model config snapshot hash
- preprocessing/postprocessing version strings if text normalization is added later

### Run Reuse

- Always regenerate outputs; do not reuse/dedupe identical runs.

Important nuance:

- "Always regenerate" should still be compatible with idempotency. Prevent accidental double-run
  for the same battle due to refresh/retry.

### Exports

- Provide admin-only JSONL export endpoints (tasks/runs/votes) for dataset building.

Export notes (best assumptions):

- Include an explicit schema_version field in export records.
- Prefer self-contained exports (task text + rendered messages + params + outputs + usage).
- Keep model secrets out of exports; include model ids and redacted metadata only.

## Implementation Notes (Best Assumptions)

OpenAI-compatible upstream:

- Default to chat-style requests (e.g., `/v1/chat/completions`) unless the gateway requires a
  different path.
- Use streaming when available and forward deltas to the UI via SSE.
- Record the exact JSON sent upstream for each Run.

Concurrency (no workers):

- Create battle + run rows first, then execute two model calls concurrently.
- Ensure only one execution happens per battle across workers (e.g., advisory lock by battle id, or an
  atomic DB status transition from `pending` -> `running`).

Timeouts/retries:

- Apply conservative timeouts; record errors in Run.error_text.
- Retries should be limited and idempotent; do not silently create duplicate runs.

## Security Notes (Important)

You selected:

- Arbitrary `base_url` registration (high risk)
- Allow internal/private network targets (even higher SSRF blast radius)

Policy note (2026-02-18):

- Keep application-level `base_url` checks permissive (including internal IP/host targets)
  because internal gateway routing is required in this deployment.
- Mitigation is operational/network-layer controls (egress policy, admin access controls),
  not URL blocklists in the API.

This implies the deployment must assume a compromised admin account can pivot to internal
services unless strong network controls are in place.

Minimum recommended mitigations:

- Kubernetes egress NetworkPolicies: only allow pods to talk to intended egress targets.
- Explicitly block link-local + metadata IP ranges.
- DNS rebinding defenses if any hostname resolution is allowed.
- Restrict admin access via Authentik group + additional protections (MFA, IP allowlists, etc.).

Additional notes given "internal base_url allowed":

- Treat the model registry as a network-egress configuration surface.
- Keep `base_url` admin-only; never return it to public clients.
- Consider adding a future `network_scope` flag per model (internal/external) to re-enable
  stronger SSRF protections for external-only models.
- At minimum, explicitly block cloud metadata endpoints even if internal traffic is allowed.

Secrets notes:

- Store provider/gateway tokens encrypted at rest in Postgres.
- Prefer key rotation for `ARENA_MASTER_KEY` (plan for a migration path).
- Do not log Authorization headers or full upstream responses that might contain secrets.

Privacy notes:

- All votes are tied to authenticated user IDs, simplifying privacy requirements.
  Legacy anonymous vote records (from the earlier anonymous-allowed model) retain
  salted hashes of IP and User-Agent for backward compatibility.
- User profiles (language certificates/experience) are optional but sensitive; limit access and
  plan deletion/export for users.

Content/legal notes:

- Curated public tasks reduce copyright risk, but still require a provenance process.
- Comments are user-generated content; plan moderation and takedown.

## Brainstorm / Future Extensions (Non-blocking)

Translation-specific evaluation:

- Diff/highlight view between A/B.
- Task-level glossary/terminology constraints injected into prompts.
- Multi-turn consistency mode (names/honorifics consistent across segments).
- Style target tags (formal/colloquial/VN tone) and tag-specific leaderboards.

Voter quality and trust:

- Calibration tasks (golden references) to estimate voter reliability.
- Trust tiers: logged-in < verified translators.
- Offline vote weighting experiments (keep raw votes unchanged).

Sampling/ratings:

- Stratified sampling by task tags (dialogue vs narration, fantasy terms, etc.).
- Consider Bradley-Terry or Glicko-2 once vote volume grows.
- Confidence intervals and time-decayed leaderboards.

Operational:

- Cancellation and timeout handling for long translations.
- Per-model budget stops and alerting.
- Export automation (scheduled JSONL dumps) for dataset building.

## Open Items / TBD

- Weighted sampling algorithm details (pending FastChat reference).
- Exact upstream gateway streaming format and how usage stats are returned.
- Admin UX: whether to expose `base_url` publicly (likely no, to avoid leaking internals).
- Authentik claims mapping details for admin gating (which claim contains groups/roles).
