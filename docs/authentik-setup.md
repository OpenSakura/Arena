# OIDC Setup (Authentik Example)

This repo uses OIDC for login (frontend SPA PKCE) and JWT verification (backend).
The local example below uses Authentik as the identity provider. Login is required
for all mutations (creating battles, voting, retrying battles).
Leaderboard reads and completed battle result pages remain accessible without
login. Key details:

- All votes are stored with `voter_user_id` from the authenticated session.
- Admin endpoints require membership in an Authentik group (default:
  `arena_admin`).

## URLs And Redirects

The SPA handles OIDC callbacks at these paths (served by the SPA router, not the
backend):

- `/auth/callback` (authorization code redirect)
- `/auth/silent-callback` (silent token renewal)
- `/auth/logout-callback` (post-logout redirect)

Make sure your Authentik OAuth2/OIDC Provider includes the full callback URL as
a redirect URI, for example `http://localhost:5173/auth/callback`.

## Create Provider + Application (Authentik)

High-level steps (names vary slightly by Authentik version):

1. Create an **OAuth2/OpenID Provider** (authorization code flow with PKCE).
2. Create an **Application** and bind it to the provider.
3. Add a **Redirect URI** matching your SPA origin + `/auth/callback`:
   - `http://localhost:5173/auth/callback`
4. Ensure the provider issues a **JWT access token** (the backend validates the
   bearer token via the issuer JWKS).
5. No client secret is needed. The SPA uses a public PKCE client.

## Admin Gating (Backend OIDC Groups -> Frontend `/me.is_admin`)

Admin access is determined entirely on the backend:

1. The backend reads the user's OIDC access token and looks for a group claim
   (default claim name: `groups`, configured by `OIDC_ADMIN_GROUP_CLAIM`).
2. If the claim contains the required group (default: `arena_admin`, configured
   by `OIDC_ADMIN_GROUP_NAME`), the user is considered an admin.
3. The `/me` endpoint exposes this result as `is_admin: true | false`.
4. The frontend fetches `/me` and uses the `is_admin` boolean to show or hide
   admin UI. It never parses group claims itself.

In Authentik, add a Property Mapping that includes group membership in the
access token, using the same claim name you configure in the backend.

## Environment Variables

The SPA itself has no server-side env vars. All public OIDC values are served
by the backend at `GET /api/v1/public-config`. Configure these in the backend:

- `OIDC_ISSUER` (must support OIDC discovery)
- `FRONTEND_OIDC_CLIENT_ID` (public SPA client id)
- `FRONTEND_OIDC_SCOPE` (default: `openid email profile offline_access`)
- `FRONTEND_OIDC_REDIRECT_PATH` (default: `/auth/callback`)
- `FRONTEND_OIDC_SILENT_REDIRECT_PATH` (default: `/auth/silent-callback`)
- `FRONTEND_OIDC_POST_LOGOUT_REDIRECT_PATH` (default: `/auth/logout-callback`)

No admin-specific frontend env vars are needed. The frontend determines admin
status from the `/me` endpoint's `is_admin` field (see above).

Backend (`backend/.env`):

- `OIDC_ISSUER=...` (must support OIDC discovery)
- `OIDC_AUDIENCE=...` (optional; only set if you want to enforce `aud`)
- `OIDC_ADMIN_GROUP_CLAIM=groups`
- `OIDC_ADMIN_GROUP_NAME=arena_admin`

## Troubleshooting

- `401 Authentication required` on `/me/profile`:
  - Your frontend is not sending the bearer access token, or the token is not a
    JWT the backend can validate.
- `403 Admin access required` on backend admin endpoints (e.g. `/api/v1/admin/*`):
  - Your access token does not contain the configured group claim, or the user
     is not in the configured admin group.
- OIDC discovery errors:
  - Double-check `OIDC_ISSUER`. It must match the issuer in
     `/.well-known/openid-configuration`.

Issuer note:

- The backend normalizes trailing slashes when validating the `iss` claim.
  `https://auth.example.com/application/o/arena` and the same URL with a trailing
  `/` are treated as equivalent.
