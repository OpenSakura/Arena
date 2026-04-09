<!--
todo.md

Project task list for OpenSakura Arena.

Notes:
- This file mirrors the in-chat TODO list so it can be tracked in-repo.
- Use this as an implementation checklist; keep items small and verifiable.
- Key alignment decisions live in `docs/arena-alignment.md`.
-->

# TODO

Legend:
- P0: MVP required for a usable arena.
- P1: hardening/ops/security improvements.
- P2: research/product extensions.

## P0 - MVP End-to-End

- [x] Scaffold monorepo source tree (backend/ + frontend/ + infra/) with file headers

Backend (FastAPI + Postgres)

- [x] Alembic: add `backend/migrations/env.py` + `backend/migrations/script.py.mako`
- [x] Alembic: generate initial schema revision from ORM models
- [x] DB: add constraints + indexes (unique user key on (issuer, sub), run/battle indexes, etc.)
- [x] OIDC (Authentik): verify JWT via issuer discovery + JWKS cache
- [x] OIDC: upsert `users` row keyed by (issuer, sub); return `/me` response
- [x] Admin auth: enforce Authentik group claim (e.g. `arena_admin`) for `/api/v1/admin/*`

- [x] Model registry: CRUD endpoints (admin-only)
- [x] Model registry: encrypt/decrypt per-model API key at rest (Postgres)
- [x] Model registry: support per-model OpenAI params: temperature/frequency_penalty/presence_penalty/extra_body
- [x] Model registry: keep `base_url` admin-only in responses (never return to public clients)

- [x] Prompt templates: CRUD/versioning + content hashing (admin-only)
- [x] Prompt templates: per-model full template binding (system definition)
- [x] Prompt rendering: implement safe renderer (avoid unsafe `str.format` for untrusted templates)

- [x] Tasks/task sets: CRUD endpoints (admin-only)
- [x] Tasks import: JSONL import endpoint for curated public passages
- [x] Tasks storage: snapshot exact JP text in Postgres + provenance metadata

- [x] Battle create: select task + model pair (weighted sampling, pending FastChat reference)
- [x] Battle create: create `battle` + two `run` rows before calling upstream
- [x] LLM client: implement OpenAI-compatible streaming client (httpx) for all models
- [x] Battle stream: SSE endpoint that streams A/B deltas to the frontend
- [x] Battle logging: persist raw output + usage stats + latency + request ids when present
- [x] Idempotency: prevent accidental double-run for same battle (refresh/retry)
- [x] Always regenerate: do not reuse cached outputs across battles
- [x] Translation-only output: enforce via prompts (and optionally reject obvious non-translation wrappers)

- [x] Voting: submit vote (A/B/tie) + rubric tags + comment
- [x] Voting (anonymous): store anon id + salted hashes of IP and User-Agent
- [x] Voting (anti-abuse): verify Cloudflare Turnstile token for anonymous votes
- [x] Voting (logged-in): store `voter_user_id` when available; no rate limit by policy
- [x] Ratings: Elo updates + persist rating events
- [x] Reveal: return model identities after vote submit

- [x] Leaderboard: basic leaderboard endpoint (rating + games played)

- [x] Export: admin-only JSONL export endpoints for tasks/runs/battles/votes/ratings
- [x] Export: include schema_version and avoid exporting secrets

Frontend (Next.js)

- [ ] Authentik OIDC: verify NextAuth config works end-to-end (login/logout)
- [x] Header: show auth state + login/logout controls

- [x] Onboarding (optional): profile form (JLPT, experience per language pair/role)
- [x] Onboarding: persist to backend `/me/profile`

- [x] Battle page: create battle (or load existing), then start SSE stream
- [x] Battle page: render JP source + A/B outputs side-by-side
- [x] Battle page: voting UI (A/B/tie + rubric tags + comment)
- [x] Battle page: Turnstile integration for anonymous votes
- [x] Battle page: reveal modal after vote (model A/B identities + basic stats)

- [x] Leaderboard page: fetch and render leaderboard

- [x] Admin UI: models CRUD (including temp/penalties/extra_body) + model test call
- [x] Admin UI: prompt templates CRUD/versioning
- [x] Admin UI: tasks/task sets CRUD + JSONL import

Dev/Docs

- [x] Document Authentik setup (issuer url, client config, groups claim mapping)
- [x] Add runbooks for local dev (Postgres compose + backend + frontend)

## P1 - Security, Reliability, Ops

- [ ] SSRF hardening (policy-deferred): keep `base_url` unrestricted for internal IP routing; rely on egress network controls + strong admin access controls
- [ ] Network policy guidance: document required K8s egress restrictions for production
- [x] Rate limiting: implement anonymous throttles for battle creation + vote submission
- [ ] Abuse tooling: basic admin view for suspicious anon ids / high-frequency IP hashes

- [ ] Observability: request correlation id + structured logs
- [ ] Error handling: clear user-facing errors for model timeouts/failures
- [ ] Retention: plan/implement partitions or retention job for high-volume run logs
- [x] Leaderboard reliability: periodic refresh job + admin status/manual refresh endpoint

## P2 - Product/Research Extensions

- [x] Weighted sampling: implement FastChat-inspired algorithm (after you provide reference)
- [ ] Task tagging: dialogue/narration/fantasy terms tags + stratified sampling
- [ ] Diff/highlight: add A/B diff view for translation comparison
- [ ] Trust tiers: optional vote weighting and calibration tasks (offline analysis)
- [x] Alternative ratings: Bradley-Terry + confidence intervals
- [ ] Alternative ratings: Glicko-2 + confidence intervals
