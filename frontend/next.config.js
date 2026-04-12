/**
 * frontend/next.config.js
 *
 * Next.js configuration.
 *
 * Notes:
 * - Keep config minimal; prefer app-level settings.
 */

const backendUrl = process.env.NEXT_PUBLIC_BACKEND_URL || "http://localhost:8000";
const backendOrigin = (() => {
  try {
    const u = new URL(backendUrl);
    return u.origin;
  } catch {
    return backendUrl;
  }
})();

const oidcOrigins = (process.env.NEXT_PUBLIC_OIDC_ORIGINS || "")
  .split(",")
  .map((s) => s.trim())
  .filter(Boolean);

const isProduction = process.env.NODE_ENV === "production";

const connectSrcParts = ["'self'", backendOrigin, ...oidcOrigins];

const scriptSrcParts = [
  "'self'",
  "'unsafe-inline'",
  ...(!isProduction ? ["'unsafe-eval'"] : []),
  "https://challenges.cloudflare.com",
];

/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,
  async headers() {
    return [
      {
        source: "/(.*)",
        headers: [
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "X-Frame-Options", value: "DENY" },
          { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
          {
            key: "Permissions-Policy",
            value: "camera=(), microphone=(), geolocation=()",
          },
          {
            key: "Content-Security-Policy",
            value: [
              "default-src 'self'",
              "script-src " + scriptSrcParts.join(" "),
              "style-src 'self' 'unsafe-inline'",
              "img-src 'self' data: blob:",
              "font-src 'self'",
              "connect-src " + connectSrcParts.join(" "),
              "frame-src https://challenges.cloudflare.com",
              "base-uri 'self'",
              "worker-src 'self'",
            ].join("; "),
          },
        ],
      },
    ];
  },
};

module.exports = nextConfig;
