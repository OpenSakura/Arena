/**
 * frontend/src/app/api/auth/[...nextauth]/route.ts
 *
 * NextAuth route handler (Auth.js) for OIDC login via Authentik.
 *
 * Notes:
 * - Anonymous usage is allowed; this exists to optionally enrich votes.
 * - The access token should be forwarded to the backend as `Authorization`.
 * - Token refresh is handled by storing the refresh_token and re-acquiring
 *   an access_token before expiry.
 */

import NextAuth from "next-auth";
import AuthentikProvider from "next-auth/providers/authentik";

const nextAuthSecret = process.env.NEXTAUTH_SECRET;
if (
  process.env.NODE_ENV === "production" &&
  (!nextAuthSecret || nextAuthSecret.length < 32)
) {
  throw new Error(
    "NEXTAUTH_SECRET must be set to a secure random value (>= 32 chars) in production",
  );
}

const authentikClientId = process.env.AUTHENTIK_CLIENT_ID;
const authentikClientSecret = process.env.AUTHENTIK_CLIENT_SECRET;
if (
  process.env.NODE_ENV === "production" &&
  (!authentikClientId || !authentikClientSecret)
) {
  throw new Error(
    "AUTHENTIK_CLIENT_ID and AUTHENTIK_CLIENT_SECRET must be set in production",
  );
}

const handler = NextAuth({
  secret: nextAuthSecret,
  providers: [
    AuthentikProvider({
      issuer: process.env.AUTHENTIK_ISSUER,
      clientId: authentikClientId ?? "",
      clientSecret: authentikClientSecret ?? "",
      authorization: { params: { scope: "openid email profile offline_access" } },
    }),
  ],
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

async function refreshAccessToken(token: Record<string, unknown>): Promise<Record<string, unknown>> {
  const issuer = process.env.AUTHENTIK_ISSUER;
  const clientId = process.env.AUTHENTIK_CLIENT_ID ?? "";
  const clientSecret = process.env.AUTHENTIK_CLIENT_SECRET ?? "";

  if (!issuer || !token.refreshToken) {
    return { ...token, error: "RefreshTokenMissing" };
  }

  try {
    // Discover the token endpoint from the OIDC issuer.
    const discoveryUrl = `${issuer.replace(/\/$/, "")}/.well-known/openid-configuration`;
    const discoveryRes = await fetch(discoveryUrl, {
      signal: AbortSignal.timeout(10_000),
    });
    if (!discoveryRes.ok) {
      return { ...token, error: "RefreshDiscoveryFailed" };
    }
    const discovery = (await discoveryRes.json()) as { token_endpoint?: string };
    const tokenEndpoint = discovery.token_endpoint;
    if (!tokenEndpoint) {
      return { ...token, error: "RefreshDiscoveryFailed" };
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
      return { ...token, error: "RefreshTokenExpired" };
    }

    return {
      ...token,
      accessToken: refreshed.access_token,
      refreshToken: refreshed.refresh_token ?? token.refreshToken,
      accessTokenExpires: Date.now() + (refreshed.expires_in ?? 3600) * 1000,
      error: undefined,
    };
  } catch {
    return { ...token, error: "RefreshTokenError" };
  }
}

export { handler as GET, handler as POST };
