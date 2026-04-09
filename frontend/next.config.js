/**
 * frontend/next.config.js
 *
 * Next.js configuration.
 *
 * Notes:
 * - Keep config minimal; prefer app-level settings.
 */

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
              "script-src 'self' 'unsafe-inline' 'unsafe-eval' https://challenges.cloudflare.com",
              "style-src 'self' 'unsafe-inline'",
              "img-src 'self' data: blob:",
              "font-src 'self'",
              "connect-src 'self' " + (process.env.NEXT_PUBLIC_BACKEND_URL || "http://localhost:8000"),
              "frame-src https://challenges.cloudflare.com",
            ].join("; "),
          },
        ],
      },
    ];
  },
};

module.exports = nextConfig;
