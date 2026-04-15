/**
 * frontend/src/app/api/auth/[...nextauth]/route.ts
 *
 * NextAuth route handler (Auth.js) for generic OIDC login.
 *
 * Notes:
 * - Anonymous usage is allowed; this exists to optionally enrich votes.
 * - The access token should be forwarded to the backend as `Authorization`.
 * - Token refresh is handled by storing the refresh_token and re-acquiring
 *   an access_token before expiry.
 */

import NextAuth from "next-auth";

type OIDCProfile = {
  sub: string;
  name?: string | null;
  preferred_username?: string | null;
  email?: string | null;
  picture?: string | null;
};

type OIDCCheck = "pkce" | "state";

const nextAuthSecret = process.env.NEXTAUTH_SECRET;
if (
  process.env.NODE_ENV === "production" &&
  (!nextAuthSecret || nextAuthSecret.length < 32)
) {
  throw new Error(
    "NEXTAUTH_SECRET must be set to a secure random value (>= 32 chars) in production",
  );
}

const oidcIssuer = process.env.OIDC_ISSUER ?? process.env.AUTHENTIK_ISSUER;
const oidcClientId = process.env.OIDC_CLIENT_ID ?? process.env.AUTHENTIK_CLIENT_ID;
const oidcClientSecret =
  process.env.OIDC_CLIENT_SECRET ?? process.env.AUTHENTIK_CLIENT_SECRET;
if (
  process.env.NODE_ENV === "production" &&
  (!oidcClientId || !oidcClientSecret)
) {
  throw new Error(
    "OIDC_CLIENT_ID and OIDC_CLIENT_SECRET must be set in production",
  );
}

const oidcChecks: OIDCCheck[] = ["pkce", "state"];

const oidcProvider = {
  id: "oidc",
  name: "OIDC",
  type: "oauth" as const,
  issuer: oidcIssuer,
  wellKnown: oidcIssuer
    ? `${oidcIssuer.replace(/\/$/, "")}/.well-known/openid-configuration`
    : undefined,
  clientId: oidcClientId ?? "",
  clientSecret: oidcClientSecret ?? "",
  authorization: { params: { scope: "openid email profile offline_access" } },
  checks: oidcChecks,
  profile(profile: OIDCProfile) {
    return {
      id: profile.sub,
      name:
        profile.name ??
        profile.preferred_username ??
        profile.email ??
        profile.sub,
      email: profile.email,
      image: profile.picture,
    };
  },
};

const handler = NextAuth({
  secret: nextAuthSecret,
  providers: [oidcProvider],
  callbacks: {
    async jwt({ token, account }) {
      // Persist tokens on initial sign-in.
      if (account) {
        token.accessToken = account.access_token;
        token.refreshToken = account.refresh_token;
        token.accessTokenExpires = account.expires_at
          ? account.expires_at * 1000
          : Date.now() + 3600 * 1000;
        return token;
      }

      // Return the existing token if it hasn't expired yet (with 60s buffer).
      if (
        typeof token.accessTokenExpires === "number" &&
        Date.now() < token.accessTokenExpires - 60_000
      ) {
        return token;
      }

      // Attempt to refresh the access token.
      return await refreshAccessToken(token);
    },
    async session({ session, token }) {
      session.accessToken = token.accessToken;
      if (token.error) {
        session.error = token.error;
      }
      return session;
    },
  },
});

let cachedTokenEndpoint: { url: string; expiresAt: number } | null = null;

async function getTokenEndpoint(issuer: string): Promise<string | null> {
  if (cachedTokenEndpoint && Date.now() < cachedTokenEndpoint.expiresAt) {
    return cachedTokenEndpoint.url;
  }

  const discoveryUrl = `${issuer.replace(/\/$/, "")}/.well-known/openid-configuration`;
  const discoveryRes = await fetch(discoveryUrl, {
    signal: AbortSignal.timeout(10_000),
  });
  if (!discoveryRes.ok) {
    return null;
  }
  const discovery = (await discoveryRes.json()) as { token_endpoint?: string };
  const endpoint = discovery.token_endpoint;
  if (!endpoint) {
    return null;
  }

  cachedTokenEndpoint = { url: endpoint, expiresAt: Date.now() + 3600_000 };
  return endpoint;
}

async function refreshAccessToken(token: Record<string, unknown>): Promise<Record<string, unknown>> {
  const issuer = oidcIssuer;
  const clientId = oidcClientId ?? "";
  const clientSecret = oidcClientSecret ?? "";

  function expiredToken(error: string): Record<string, unknown> {
    return {
      ...token,
      accessToken: undefined,
      accessTokenExpires: 0,
      error,
    };
  }

  if (!issuer || !token.refreshToken) {
    return expiredToken("RefreshTokenMissing");
  }

  try {
    const tokenEndpoint = await getTokenEndpoint(issuer);
    if (!tokenEndpoint) {
      return expiredToken("RefreshDiscoveryFailed");
    }

    const res = await fetch(tokenEndpoint, {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams({
        grant_type: "refresh_token",
        client_id: clientId,
        client_secret: clientSecret,
        refresh_token: token.refreshToken as string,
      }),
      signal: AbortSignal.timeout(10_000),
    });

    const refreshed = (await res.json()) as {
      access_token?: string;
      refresh_token?: string;
      expires_in?: number;
      error?: string;
    };

    if (!res.ok || refreshed.error || !refreshed.access_token) {
      return expiredToken("RefreshTokenExpired");
    }

    return {
      ...token,
      accessToken: refreshed.access_token,
      refreshToken: refreshed.refresh_token ?? token.refreshToken,
      accessTokenExpires: Date.now() + (refreshed.expires_in ?? 3600) * 1000,
      error: undefined,
    };
  } catch {
    return expiredToken("RefreshTokenError");
  }
}

export { handler as GET, handler as POST };
