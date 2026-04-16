# OpenSakura Arena

This repository is a monorepo for a translation-judgment "arena" focused on
Japanese (JP) -> Chinese (ZH) light novel / visual novel style translations.

Notes:
- `backend/` is a FastAPI control-plane API backed by Postgres.
- `frontend/` is a React + Vite SPA for battles, voting, leaderboards, and admin.
- Auth uses OIDC (SPA PKCE flow). Public pages (battles, leaderboards, results)
  can be browsed without login. Creating battles, submitting votes, retrying
  battles, and revealing votes all require authentication.
- The frontend is deployed as a static bundle behind a reverse proxy that
  routes `/api/v1` to the FastAPI backend on the same origin.

Docs:
- Local dev: `docs/local-dev.md`
- OIDC setup (Authentik example): `docs/authentik-setup.md`
- Alignment/spec notes: `docs/arena-alignment.md`
- Project checklist: `todo.md`
