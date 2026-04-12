/**
 * frontend/src/middleware.ts
 *
 * NextAuth edge middleware that protects /admin/* routes.
 *
 * Unauthenticated requests to any /admin path are redirected to /?callbackUrl=<path>
 * by the NextAuth default middleware behaviour (NextAuth v4 + next-auth/middleware).
 *
 * Notes:
 * - Only the matcher matters here; the default export handles the redirect logic.
 * - NextAuth reads the session cookie and redirects to the configured `pages.signIn`
 *   URL (defaults to /api/auth/signin) when no session is found.  We override that
 *   with `pages: { signIn: "/" }` so the redirect lands on the home page with a
 *   `callbackUrl` query param, matching what the e2e tests assert.
 */

import { withAuth } from "next-auth/middleware";

export default withAuth({
  pages: {
    signIn: "/",
  },
});

export const config = {
  matcher: ["/admin/:path*"],
};
