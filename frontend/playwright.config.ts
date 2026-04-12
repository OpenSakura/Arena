import path from "node:path";

import { defineConfig } from "@playwright/test";

const FRONTEND_PORT = 13000;
const BACKEND_PORT = 8000;
const MOCK_LLM_PORT = 18080;

const REPO_ROOT = path.resolve(process.cwd(), "..");
const BACKEND_DIR = path.join(REPO_ROOT, "backend");

const FRONTEND_ORIGIN = `http://localhost:${FRONTEND_PORT}`;
const BACKEND_BASE_URL = `http://localhost:${BACKEND_PORT}/api/v1`;
const ENABLE_LIVE_STACK = process.env.PW_ENABLE_LIVE_STACK === "1";

const frontendServer = {
  command: `npm run build && npm run start -- --port ${FRONTEND_PORT}`,
  url: FRONTEND_ORIGIN,
  timeout: 240_000,
  reuseExistingServer: false,
  env: {
    ...process.env,
    NEXTAUTH_URL: FRONTEND_ORIGIN,
    NEXTAUTH_SECRET: "arena-frontend-e2e-nextauth-secret",
    AUTHENTIK_ISSUER: "http://localhost:19000/application/o/arena-e2e",
    AUTHENTIK_CLIENT_ID: "arena-e2e-client",
    AUTHENTIK_CLIENT_SECRET: "arena-e2e-secret",
    NEXT_PUBLIC_BACKEND_URL: BACKEND_BASE_URL,
    NEXT_PUBLIC_TURNSTILE_SITE_KEY:
      process.env.NEXT_PUBLIC_TURNSTILE_SITE_KEY ?? "1x00000000000000000000AA",
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
    url: `${BACKEND_BASE_URL}/healthz`,
    timeout: 240_000,
    reuseExistingServer: false,
    env: {
      ...process.env,
      APP_ENV: "test",
      LOG_LEVEL: "warning",
      LEADERBOARD_REFRESH_ENABLED: "false",
      DATABASE_URL: "postgresql+psycopg://postgres:postgres@localhost:15432/arena_e2e",
      RATE_LIMIT_REDIS_URL: "redis://localhost:16379/15",
      CORS_ALLOW_ORIGINS: FRONTEND_ORIGIN,
      OIDC_ISSUER: "http://localhost:19000/application/o/arena-e2e/",
      OIDC_AUDIENCE: "arena-e2e-client",
      ANON_BATTLE_CREATE_RATE_LIMIT: "1000",
      ANON_BATTLE_CREATE_RATE_LIMIT_WINDOW_SECONDS: "60",
      ANON_VOTE_SUBMIT_RATE_LIMIT: "1000",
      ANON_VOTE_SUBMIT_RATE_LIMIT_WINDOW_SECONDS: "60",
      ANON_IP_HASH_SALT: "arena-frontend-e2e-ip-salt",
      ANON_USER_AGENT_HASH_SALT: "arena-frontend-e2e-ua-salt",
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
