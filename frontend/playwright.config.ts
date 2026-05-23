import path from "node:path";

import { defineConfig } from "@playwright/test";

const FRONTEND_PORT = 13000;
const BACKEND_PORT = Number(process.env.PW_BACKEND_PORT ?? 28000);
const MOCK_LLM_PORT = Number(process.env.PW_MOCK_LLM_PORT ?? 18080);
const PLAYWRIGHT_POSTGRES_PORT = 25432;
const PLAYWRIGHT_REDIS_PORT = 26379;
const PLAYWRIGHT_AUTHENTIK_PORT = 29000;

const REPO_ROOT = path.resolve(process.cwd(), "..");
const BACKEND_DIR = path.join(REPO_ROOT, "backend");

const FRONTEND_ORIGIN = `http://localhost:${FRONTEND_PORT}`;
const BACKEND_ORIGIN = `http://127.0.0.1:${BACKEND_PORT}`;
const BACKEND_BASE_URL = `${BACKEND_ORIGIN}/api/v1`;
const ENABLE_LIVE_STACK = process.env.PW_ENABLE_LIVE_STACK === "1";

const PLAYWRIGHT_LIVE_STACK_PORT_ENV = {
  ARENA_POSTGRES_HOST_PORT: String(PLAYWRIGHT_POSTGRES_PORT),
  ARENA_REDIS_HOST_PORT: String(PLAYWRIGHT_REDIS_PORT),
  ARENA_AUTHENTIK_HOST_PORT: String(PLAYWRIGHT_AUTHENTIK_PORT),
};

const frontendServer = {
  command: `npx vite --host 127.0.0.1 --strictPort --port ${FRONTEND_PORT}`,
  url: FRONTEND_ORIGIN,
  timeout: 240_000,
  reuseExistingServer: false,
  env: {
    ...process.env,
    ...(ENABLE_LIVE_STACK ? { VITE_DEV_PROXY_TARGET: BACKEND_ORIGIN } : {}),
  },
};

const liveStackServers = [
  {
    command: "node ./e2e/mock-openai-server.mjs",
    url: `http://localhost:${MOCK_LLM_PORT}/healthz`,
    timeout: 60_000,
    reuseExistingServer: false,
    env: {
      ...process.env,
      ...PLAYWRIGHT_LIVE_STACK_PORT_ENV,
      MOCK_LLM_PORT: String(MOCK_LLM_PORT),
    },
  },
  {
    command:
      `docker compose -f tests/e2e/docker-compose.yaml -p arena-frontend-e2e up -d --wait && ` +
      `uv run python -m app.db.bootstrap && ` +
      `uv run python tests/e2e/seed_frontend_playwright.py && ` +
      `uv run uvicorn app.main:create_app --factory --host 127.0.0.1 --port ${BACKEND_PORT}`,
    cwd: BACKEND_DIR,
    url: `${BACKEND_BASE_URL}/readyz`,
    timeout: 240_000,
    reuseExistingServer: false,
    env: {
      ...process.env,
      ...PLAYWRIGHT_LIVE_STACK_PORT_ENV,
      APP_ENV: "test",
      LOG_LEVEL: "warning",
      LEADERBOARD_REFRESH_ENABLED: "true",
      LEADERBOARD_REFRESH_INTERVAL_SECONDS: "5",
      DATABASE_URL: `postgresql+psycopg://postgres:postgres@localhost:${PLAYWRIGHT_POSTGRES_PORT}/arena_e2e`,
      RATE_LIMIT_REDIS_URL: `redis://localhost:${PLAYWRIGHT_REDIS_PORT}/15`,
      CORS_ALLOW_ORIGINS: FRONTEND_ORIGIN,
      OIDC_ISSUER: `http://localhost:${PLAYWRIGHT_AUTHENTIK_PORT}/application/o/arena-e2e/`,
      OIDC_AUDIENCE: "arena-e2e-client",
      FRONTEND_OIDC_CLIENT_ID: "arena-e2e-client",
      ARENA_MASTER_KEY: "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY=",
      PLAYWRIGHT_MOCK_LLM_BASE_URL: `http://127.0.0.1:${MOCK_LLM_PORT}`,
    },
  },
  frontendServer,
];

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  workers: 1,
  timeout: 120_000,
  expect: {
    timeout: 15_000,
  },
  reporter: "list",
  globalSetup: "./e2e/global-setup.ts",
  globalTeardown: "./e2e/global-teardown.ts",
  use: {
    baseURL: `http://localhost:${FRONTEND_PORT}`,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "retain-on-failure",
  },
  webServer: ENABLE_LIVE_STACK ? liveStackServers : frontendServer,
});
