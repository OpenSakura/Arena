import { spawnSync } from "node:child_process";
import path from "node:path";

const COMPOSE_PROJECT = "arena-frontend-e2e";

function run(command: string, args: string[], cwd: string): void {
  spawnSync(command, args, {
    cwd,
    encoding: "utf-8",
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
