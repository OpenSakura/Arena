# OpenSakura Arena

This repository is a monorepo for a translation-judgment "arena" focused on
Japanese (JP) -> Chinese (ZH) light novel / visual novel style translations.

Notes:
- `backend/` is a FastAPI control-plane API backed by Postgres.
- `frontend/` is a Next.js web app for battles, voting, leaderboards, and admin.
- Auth uses OIDC. Public pages (battles, leaderboards, results)
  can be browsed without login. Creating battles, submitting votes, retrying
  battles, and revealing votes all require authentication.

Repo status:
- This is a scaffold/source tree. Many files are intentionally stubs with TODOs.

Docs:
- Local dev: `docs/local-dev.md`
- OIDC setup (Authentik example): `docs/authentik-setup.md`
- Alignment/spec notes: `docs/arena-alignment.md`
- Project checklist: `todo.md`
