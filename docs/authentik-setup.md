# OIDC Setup (Authentik Example)

OpenSakura Arena uses backend/BFF confidential OIDC for browser login. The
frontend starts login by calling the backend, and the backend owns the provider
code exchange, client secret, app session cookie, and CSRF checks.

The backend callback path is exactly `/api/v1/auth/callback`. Register that path
on the same browser-facing origin as the arena UI, for example:

- Local Vite dev: `http://localhost:5173/api/v1/auth/callback`
- Frontend e2e: `http://localhost:13000/api/v1/auth/callback`
- Production: `https://arena.example.com/api/v1/auth/callback`

Leaderboard reads remain available without login. Creating battles, viewing
battles, voting, retrying battles, profile updates, and admin actions require an
authenticated backend session.

## Architecture

- The browser never receives the OIDC client secret, authorization code verifier,
  access token, ID token, or refresh token.
- `/api/v1/auth/login` creates backend-owned state, nonce, and PKCE values, then
  redirects to Authentik.
- Authentik redirects the browser to `/api/v1/auth/callback` on the arena origin.
- The backend exchanges the code with `client_secret_basic` and retains PKCE
  `S256` server-side.
- The backend stores only an app session and sets an opaque HttpOnly session
  cookie, default `arena_session`.
- `/api/v1/auth/session` returns the current user plus a CSRF token. Unsafe
  cookie-authenticated requests must send that token in `X-CSRF-Token`.

## Create Provider And Application

High-level Authentik steps, names vary slightly by Authentik version:

1. Create an OAuth2/OpenID Provider for authorization code login.
2. Set the client type to confidential or web.
3. Set token endpoint authentication to `client_secret_basic`.
4. Keep PKCE enabled and use challenge method `S256`.
5. Register the redirect URI for the arena origin plus `/api/v1/auth/callback`.
6. Create an Application and bind it to the provider.
7. Map the admin group claim into tokens used by the backend session. The default
   claim name is `groups`, and the default admin group is `arena_admin`.

Use the provider's generated client ID and client secret only in backend/server
secret storage.

## Backend Environment

Set these in `backend/.env`, your process manager, or Kubernetes backend secret
wiring:

- `PUBLIC_BASE_URL=https://arena.example.com`
- `OIDC_ISSUER=https://auth.example.com/application/o/arena/`
- `OIDC_AUDIENCE=arena`
- `OIDC_CLIENT_ID=arena-backend`
- `OIDC_CLIENT_SECRET=<backend secret storage only>`
- `OIDC_CLIENT_AUTH_METHOD=client_secret_basic`
- `OIDC_SCOPE=openid email profile`
- `OIDC_REDIRECT_PATH=/api/v1/auth/callback`
- `AUTH_SESSION_HASH_SECRET=<backend secret storage only>`
- `AUTH_SESSION_COOKIE_NAME=arena_session`
- `AUTH_SESSION_MAX_AGE_SECONDS=28800`
- `AUTH_CSRF_HEADER_NAME=X-CSRF-Token`
- `OIDC_ADMIN_GROUP_CLAIM=groups`
- `OIDC_ADMIN_GROUP_NAME=arena_admin`

`OIDC_CLIENT_SECRET` is required backend/server-only configuration. Do not put it
in frontend, Vite, browser, public config, screenshots, or Playwright artifacts.

`AUTH_SESSION_HASH_SECRET` is also backend/server-only. The backend uses it as
the HMAC key for opaque OAuth login-state, app session, and CSRF token hashes.
Changing it invalidates existing app sessions and CSRF tokens.

`OIDC_SCOPE` defaults to `openid email profile`. Do not add `offline_access`
unless a later design adds refresh-token storage. The current backend session
architecture discards provider tokens after validation.

## Frontend Environment

The SPA has no provider secret or token endpoint credentials. It only needs the
Vite dev proxy target when local defaults do not fit:

```bash
VITE_DEV_PROXY_TARGET=http://localhost:8000
```

Runtime auth paths come from `GET /api/v1/public-config`, then the SPA calls
`GET /api/v1/auth/session` with credentials included.

## Admin Gating

Admin access is determined by the backend:

1. The backend reads the configured group claim from validated OIDC claims stored
   in the app session or from validated bearer tokens.
2. If the claim contains the configured group, default `arena_admin`, the user is
   considered an admin.
3. `/api/v1/auth/session` and `/me` expose `is_admin` to the frontend.
4. The frontend uses that boolean to show or hide admin UI. It never parses group
   claims itself.

In Authentik, add a property mapping that includes group membership in the token
claims used by the backend. Use the same claim name you configure with
`OIDC_ADMIN_GROUP_CLAIM`.

## Secret Rotation

For migration cutover, rollback, dual-mode scope, session impact, and monitoring
signals, use the [auth migration rollback runbook](auth-migration-rollback.md).

Rotate `OIDC_CLIENT_SECRET` with these steps:

1. Create or rotate the client secret in Authentik.
2. Update the backend secret source, for example a Kubernetes Secret or process
   manager secret.
3. Deploy or restart backend pods so they read the new secret.
4. Verify login reaches `/api/v1/auth/callback` and creates a backend session.
5. Revoke the old provider secret if Authentik supports overlapping secrets.
6. If Authentik cannot overlap secrets, schedule a tight cutover window and
   expect in-flight login attempts to retry.

Rotate `AUTH_SESSION_HASH_SECRET` only when you can invalidate active sessions.
Existing session, CSRF, and login-state hashes were created with the previous
key.

## Troubleshooting

- Callback returns an auth error:
  - Check that the Authentik redirect URI exactly matches the arena origin plus
    `/api/v1/auth/callback`.
  - Check `PUBLIC_BASE_URL` and `OIDC_REDIRECT_PATH` on the backend.
- Token exchange fails:
  - Confirm the provider is confidential or web, uses `client_secret_basic`, and
    the backend has the current `OIDC_CLIENT_SECRET`.
  - Keep PKCE enabled with `S256`; the backend sends the verifier during token
    exchange.
- `401 Authentication required` after login:
  - Check that the reverse proxy preserves cookies and routes `/api/v1` to the
    backend on the same origin.
- `403 CSRF token required` or `403 Invalid CSRF token`:
  - Reload the SPA so it can call `/api/v1/auth/session` and receive the latest
    CSRF token, then retry the unsafe action.
- `403 Admin access required` on backend admin endpoints:
  - Check that the configured group claim is present and contains the configured
    admin group.
- OIDC discovery errors:
  - Check `OIDC_ISSUER`. It must match the issuer in
    `/.well-known/openid-configuration`.

Issuer note:

- The backend normalizes trailing slashes when validating the `iss` claim.
  `https://auth.example.com/application/o/arena` and the same URL with a trailing
  `/` are treated as equivalent.
