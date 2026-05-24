# Auth Migration Rollback Runbook

This runbook covers operational rollback for the move from browser-owned public
SPA OIDC to backend/BFF confidential OIDC. The target architecture keeps the
client secret in backend secret storage, exchanges the authorization code at
`/api/v1/auth/callback` with `client_secret_basic`, retains PKCE `S256`
server-side, and uses an HttpOnly app session cookie with CSRF checks.

Use rollback to return to the last known-good public SPA behavior. Don't build
ad-hoc dual logic during an incident.

## Pre-cutover Checklist

Complete this before shifting traffic to the confidential-client build:

1. Create the Authentik OAuth2/OpenID provider as confidential or web.
2. Set token endpoint authentication to `client_secret_basic`.
3. Keep PKCE enabled and verify the provider accepts confidential clients with
   PKCE `S256`.
4. Register the exact redirect URI for each environment:
   `https://arena.example.com/api/v1/auth/callback`, or the matching arena
   origin plus `/api/v1/auth/callback`.
5. Configure backend/server secrets only: `OIDC_CLIENT_ID`,
   `OIDC_CLIENT_SECRET`, and `AUTH_SESSION_HASH_SECRET`.
6. Confirm `PUBLIC_BASE_URL` matches the browser-facing arena origin and
   `OIDC_REDIRECT_PATH=/api/v1/auth/callback`.
7. Confirm the frontend and Vite env contain no `OIDC_CLIENT_SECRET`, provider
   token endpoint credentials, access tokens, ID tokens, refresh tokens, nonce,
   or code verifier values.
8. Run backend tests: `cd backend && uv run pytest`.
9. Run provider-backed backend e2e: `cd backend && uv run pytest tests/e2e --run-e2e`.
10. Run the frontend auth smoke test:
    `cd frontend && npm run test:e2e -- e2e/auth.smoke.spec.ts`.
11. Deploy monitoring dashboards and alerts for the signals in the
    [Monitoring](#monitoring) section before the cutover window opens.
12. Record the prior known-good commit, image tags, frontend build artifact,
    Authentik public-client provider settings, and previous
    `/api/v1/public-config` response shape.

## User-session impact

Existing browser `sessionStorage` OIDC sessions will not survive the cutover to
backend sessions. The old `oidc.user:*` entries belonged to the SPA OIDC client,
while the new flow uses backend `AuthSession` rows plus an HttpOnly
`arena_session` cookie.

Expect some users to log in again after cutover. Users who had an old SPA OIDC
session but no backend session will be treated as unauthenticated until they
complete `/api/v1/auth/login` and return through `/api/v1/auth/callback`.

Changing `AUTH_SESSION_HASH_SECRET` also invalidates backend sessions, CSRF
tokens, and login-state hashes. Treat that change as a session reset.

## Rollback

Prefer a full rollback to a prior known-good commit or image over emergency
dual-path auth code. Use the previous public SPA provider settings and the
previous frontend build that was tested with that provider.

1. Freeze the cutover and stop new deploys while the incident owner confirms the
   prior known-good commit, backend image, frontend artifact, and Authentik
   public-client settings.
2. In Authentik, restore the previous public SPA provider settings. That means a
   public client, the former SPA redirect URI such as `/auth/callback` on the UI
   origin, the former allowed logout or silent callback URIs if they existed,
   and the prior client ID used by the SPA.
3. Restore the frontend package/build if needed. The old SPA flow depended on
   the browser OIDC client packages and the frontend build that knew how to own
   `sessionStorage` OIDC state.
4. Restore the previous `/api/v1/public-config` public-client shape from the
   prior commit. The SPA rollback build expects the old public OIDC bootstrap
   fields rather than the current backend-session auth paths.
5. Redeploy the prior known-good backend and frontend image or artifact. Don't
   hand-edit a new mixed build during the rollback.
6. Remove or disable the failed confidential-client backend secret rollout only
   after the prior image is healthy. Do not move `OIDC_CLIENT_SECRET` into
   frontend, Vite, browser env, public config, logs, or screenshots.
7. Clear failed backend-session cookies for affected users. Ask users to clear
   the site cookie named `arena_session`, or have the rollback response clear it
   with an expired cookie using `Path=/`, `HttpOnly`, and `SameSite=Lax`.
8. If users are stuck in an OAuth callback loop, also clear the short-lived
   login-state cookie, default `arena_oauth_state`, scoped to
   `/api/v1/auth/callback`.
9. Keep PKCE enabled in the restored public SPA flow. Rollback changes the
   client type and ownership model, not the PKCE security expectation.
10. Monitor login errors, callback errors, and authentication rates until they
    return to the previous baseline.

After rollback, create a follow-up incident note with the failing callback or
token endpoint symptoms, the image tags used for rollback, and the Authentik
provider settings restored.

## dual-mode Scope

Long-lived dual-mode auth is not required for the main migration. The normal
path is one cutover to backend/BFF confidential OIDC, with rollback using the
prior known-good public SPA commit or image and previous provider settings.

If deployment constraints require dual-mode, keep it narrow:

1. Put it behind an explicit feature flag owned by backend/server config.
2. Time-box the flag and document the removal date before enabling it.
3. Test both modes before release, including login, callback, logout,
   `/api/v1/public-config`, `/api/v1/auth/session`, `/me`, CSRF failures, and
   service-account or bearer-token compatibility.
4. Keep secrets backend-only. Dual-mode must never expose `OIDC_CLIENT_SECRET` to
   frontend, Vite, public config, browser storage, traces, or screenshots.
5. Do not use dual-mode as an open-ended fallback for unknown failures. If the
   confidential-client migration fails, roll back to the prior known-good public
   SPA image and provider settings.

## Monitoring

Watch these signals during cutover, rollback, and the first business day after
the change:

1. Callback failures on `/api/v1/auth/callback`, split by missing state, invalid
   state, missing login-binding cookie, provider error, and open-redirect guard.
2. Token endpoint failures from the OIDC provider, including 4xx wrong client
   auth, 4xx redirect URI mismatch, 5xx provider errors, timeout, and discovery
   failures.
3. `/api/v1/auth/session` 401 rate and total request volume.
4. `/me` authenticated versus unauthenticated rate.
5. Login conversion from `/api/v1/auth/login` start to successful session
   creation after `/api/v1/auth/callback`.
6. Logout errors from `POST /api/v1/auth/logout`, including missing or stale CSRF
   tokens.
7. CSRF 403 spikes on unsafe browser requests after session bootstrap.
8. Browser reports for repeated redirects, stuck auth error pages, or users who
   must clear cookies before login succeeds.

During rollback, keep the same monitors active until the public SPA behavior is
back at baseline. A clean rollback means callback failures drop, `/auth/session`
401s stop growing, `/me` auth rates match the prior public SPA baseline, and
login conversion returns to normal.
