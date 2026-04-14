import { spawnSync } from "node:child_process";
import path from "node:path";

const COMPOSE_PROJECT = "arena-frontend-e2e";
const PLAYWRIGHT_POSTGRES_PORT = "25432";
const PLAYWRIGHT_REDIS_PORT = "26379";
const PLAYWRIGHT_AUTHENTIK_PORT = "29000";

function run(command: string, args: string[], cwd: string): void {
  spawnSync(command, args, {
    cwd,
    encoding: "utf-8",
    env: {
      ...process.env,
      ARENA_POSTGRES_HOST_PORT: PLAYWRIGHT_POSTGRES_PORT,
      ARENA_REDIS_HOST_PORT: PLAYWRIGHT_REDIS_PORT,
      ARENA_AUTHENTIK_HOST_PORT: PLAYWRIGHT_AUTHENTIK_PORT,
    },
    stdio: "pipe",
  });
}

export default async function globalTeardown(): Promise<void> {
  const frontendDir = process.cwd();
  const repoRoot = path.resolve(frontendDir, "..");
  const composeFile = path.join(repoRoot, "backend", "tests", "e2e", "docker-compose.yaml");

  run(
    "docker",
    ["compose", "-f", composeFile, "-p", COMPOSE_PROJECT, "down", "-v", "--remove-orphans"],
    repoRoot,
  );
}
