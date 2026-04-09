# OpenSakura Arena

This repository is a monorepo for a translation-judgment "arena" focused on
Japanese (JP) -> Chinese (ZH) light novel / visual novel style translations.

Notes:
- `backend/` is a FastAPI control-plane API backed by Postgres.
- `frontend/` is a Next.js web app for battles, voting, leaderboards, and admin.
- Auth uses OIDC (Authentik). Anonymous voting is allowed; logged-in users are
  captured for downstream filtering and higher-trust analysis.

Repo status:
- This is a scaffold/source tree. Many files are intentionally stubs with TODOs.

Docs:
- Local dev: `docs/local-dev.md`
- Authentik OIDC: `docs/authentik-setup.md`
- Alignment/spec notes: `docs/arena-alignment.md`
- Project checklist: `todo.md`
