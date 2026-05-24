# OpenSakura Arena

This repository is a monorepo for a translation-judgment arena focused on
Japanese (JP) to Chinese (ZH) light novel and visual novel style translations.

Notes:
- `backend/` is a FastAPI control-plane API backed by Postgres.
- `frontend/` is a React + Vite SPA for battles, voting, leaderboards, and admin.
- Auth uses backend/BFF confidential OIDC. The backend performs the authorization
  code exchange with `client_secret_basic`, keeps PKCE `S256` server-side, and
  owns the HttpOnly app session cookie plus CSRF checks. Public pages are limited
  to the leaderboard.
- Creating and viewing battles, submitting votes, and retrying battles require
  authentication. Model identities are revealed immediately inline upon vote
  submission.
- The frontend is deployed as a static bundle behind a reverse proxy that routes
  `/api/v1` to the FastAPI backend on the same origin. The browser never receives
  the OIDC client secret or provider tokens.

Docs:
- Local dev: `docs/local-dev.md`
- OIDC setup (Authentik example): `docs/authentik-setup.md`
- Alignment/spec notes: `docs/arena-alignment.md`
- Project checklist: `todo.md`
