import { spawnSync } from "node:child_process";
import path from "node:path";

const COMPOSE_PROJECT = "arena-frontend-e2e";
const FRONTEND_PORT = 13000;

type RunOptions = {
  allowFailure?: boolean;
  cwd: string;
};

function run(command: string, args: string[], options: RunOptions): void {
  const result = spawnSync(command, args, {
    cwd: options.cwd,
    encoding: "utf-8",
    stdio: "pipe",
  });

  if (result.status === 0 || options.allowFailure) {
    return;
  }

  const stderr = result.stderr?.trim() || "(empty stderr)";
  const stdout = result.stdout?.trim() || "(empty stdout)";
  throw new Error(
    [
      `Command failed: ${command} ${args.join(" ")}`,
      `stdout:\n${stdout}`,
      `stderr:\n${stderr}`,
    ].join("\n\n"),
  );
}

function composeArgs(composeFile: string, args: string[]): string[] {
  return ["compose", "-f", composeFile, "-p", COMPOSE_PROJECT, ...args];
}

export default async function globalSetup(): Promise<void> {
  const frontendDir = process.cwd();
  const repoRoot = path.resolve(frontendDir, "..");
  const backendDir = path.join(repoRoot, "backend");
  const composeFile = path.join(repoRoot, "backend", "tests", "e2e", "docker-compose.yaml");

  try {
    run("docker", composeArgs(composeFile, ["up", "-d", "--wait"]), { cwd: repoRoot });

    const bootstrapScript = [
      "from pathlib import Path",
      "from tests.e2e.conftest import E2EStack, _bootstrap_authentik",
      `stack = E2EStack(compose_file=Path(r\"${composeFile}\"), compose_project=\"${COMPOSE_PROJECT}\")`,
      "_bootstrap_authentik(stack)",
    ].join("\n");
    run("uv", ["run", "python", "-c", bootstrapScript], { cwd: backendDir });

    const configureScript = [
      "from authentik.core.models import User",
      "from authentik.providers.oauth2.models import OAuth2Provider",
      "provider = OAuth2Provider.objects.get(name=\"arena-e2e-provider\")",
      "provider._redirect_uris = [",
      `    {\"matching_mode\": \"strict\", \"url\": \"http://localhost:${FRONTEND_PORT}/api/auth/callback/authentik\"},`,
      "]",
      "provider.save()",
      "admin = User.objects.get(username=\"akadmin\")",
      "admin.set_password(\"password1234\")",
      "admin.save()",
    ].join("\n");

    run(
      "docker",
      composeArgs(composeFile, ["exec", "-T", "authentik-server", "ak", "shell", "-c", configureScript]),
      { cwd: repoRoot },
    );
  } catch (error) {
    run(
      "docker",
      composeArgs(composeFile, ["down", "-v", "--remove-orphans"]),
      { cwd: repoRoot, allowFailure: true },
    );
    throw error;
  }
}
