# Authentik OIDC Setup

This repo uses Authentik as the OIDC provider for optional login (frontend) and
JWT verification (backend). Anonymous battles/votes are supported, but:

- Logged-in votes are stored with `voter_user_id`.
- Admin endpoints require membership in an Authentik group (default:
  `arena_admin`).

## URLs And Redirects

NextAuth (frontend) uses the callback URL:

- `http://localhost:3000/api/auth/callback/authentik`

Make sure your Authentik OAuth2/OIDC Provider includes that redirect URI.

## Create Provider + Application (Authentik)

High-level steps (names vary slightly by Authentik version):

1. Create an **OAuth2/OpenID Provider** (authorization code flow).
2. Create an **Application** and bind it to the provider.
3. Add a **Redirect URI** for NextAuth:
   - `http://localhost:3000/api/auth/callback/authentik`
4. Ensure the provider issues a **JWT access token** (the backend validates the
   bearer token via the issuer JWKS).

## Group Claim For Admin Gating

Backend admin routes are protected by a group claim in the access token.
Defaults:

- Claim name: `groups` (`OIDC_ADMIN_GROUP_CLAIM`)
- Required group: `arena_admin` (`OIDC_ADMIN_GROUP_NAME`)

In Authentik, add a Property Mapping that includes group membership in the
access token, using the same claim name you configure in the backend.

## Environment Variables

Frontend (`frontend/.env.local`):

- `NEXT_PUBLIC_BACKEND_URL=http://localhost:8000/api/v1`
- `NEXTAUTH_URL=http://localhost:3000`
- `NEXTAUTH_SECRET=...`
- `AUTHENTIK_ISSUER=...` (must support OIDC discovery)
- `AUTHENTIK_CLIENT_ID=...`
- `AUTHENTIK_CLIENT_SECRET=...`

Backend (`backend/.env`):

- `OIDC_ISSUER=...` (must support OIDC discovery)
- `OIDC_AUDIENCE=...` (optional; only set if you want to enforce `aud`)
- `OIDC_ADMIN_GROUP_CLAIM=groups`
- `OIDC_ADMIN_GROUP_NAME=arena_admin`
- `CORS_ALLOW_ORIGINS=http://localhost:3000`

## Troubleshooting

- `401 Authentication required` on `/me/profile`:
  - Your frontend is not sending the bearer access token, or the token is not a
    JWT the backend can validate.
- `403 Admin access required` on backend admin endpoints (e.g. `/api/v1/admin/*`):
  - Your access token does not contain the configured group claim, or the user
     is not in the configured admin group.
- OIDC discovery errors:
  - Double-check `AUTHENTIK_ISSUER` / `OIDC_ISSUER`. It must match the issuer in
     `/.well-known/openid-configuration`.

Issuer note:

- The backend normalizes trailing slashes when validating the `iss` claim.
  `https://auth.example.com/application/o/arena` and the same URL with a trailing
  `/` are treated as equivalent.
