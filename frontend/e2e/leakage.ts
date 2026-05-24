import fs from "node:fs/promises";
import path from "node:path";

import { expect, type Page, type TestInfo } from "@playwright/test";

const FORBIDDEN_AUTH_ARTIFACTS = [
  ["OIDC", "CLIENT", "SECRET"].join("_"),
  ["client", "secret"].join("_"),
  ["access", "token"].join("_"),
  ["refresh", "token"].join("_"),
  ["id", "token"].join("_"),
  "oidc.user",
  ["arena", "e2e", "confidential", "client", "secret"].join("-"),
  ["arena", "e2e", "auth", "session", "hash", "secret"].join("-"),
] as const;

type BrowserLeakageSnapshot = {
  label: string;
  localStorage: string;
  sessionStorage: string;
  cookie: string;
  pageText: string;
};

type ArtifactRootScan = {
  root: string;
  status: "scanned" | "absent" | "skipped";
  reason?: string;
  filesScanned: number;
  filesSkipped: Array<{ file: string; reason: string }>;
};

export type AuthLeakageAudit = {
  browser: BrowserLeakageSnapshot;
  artifactHits: Array<{ file: string; terms: string[] }>;
  artifactRoots: ArtifactRootScan[];
};

function findForbidden(value: string): string[] {
  const lower = value.toLowerCase();
  return FORBIDDEN_AUTH_ARTIFACTS.filter((term) => lower.includes(term.toLowerCase()));
}

export function expectNoForbiddenAuthText(value: string, label: string): void {
  expect(findForbidden(value), `${label} forbidden auth leakage`).toEqual([]);
}

function artifactRootCandidates(testInfo?: TestInfo): string[] {
  const candidates = [
    testInfo?.outputDir,
    path.resolve(process.cwd(), "test-results"),
    path.resolve(process.cwd(), "playwright-report"),
    path.resolve(process.cwd(), "..", ".playwright-mcp"),
  ].filter((value): value is string => Boolean(value));
  return [...new Set(candidates)];
}

async function listFiles(root: string): Promise<string[]> {
  const entries = await fs.readdir(root, { withFileTypes: true });
  const nested = await Promise.all(entries.map(async (entry) => {
    const absolute = path.join(root, entry.name);
    if (entry.name.startsWith(".playwright-artifacts-")) return [];
    if (entry.isDirectory()) return listFiles(absolute);
    if (entry.isFile()) return [absolute];
    return [];
  }));
  return nested.flat();
}

function isLikelyText(buffer: Buffer): boolean {
  if (buffer.includes(0)) return false;
  return !buffer.toString("utf8").includes("\uFFFD");
}

async function scanArtifactRoots(testInfo?: TestInfo): Promise<Pick<AuthLeakageAudit, "artifactHits" | "artifactRoots">> {
  const artifactHits: AuthLeakageAudit["artifactHits"] = [];
  const artifactRoots: ArtifactRootScan[] = [];

  for (const root of artifactRootCandidates(testInfo)) {
    let stat;
    try {
      stat = await fs.stat(root);
    } catch (error) {
      artifactRoots.push({
        root,
        status: "absent",
        reason: error instanceof Error ? error.message : "not found",
        filesScanned: 0,
        filesSkipped: [],
      });
      continue;
    }

    if (!stat.isDirectory()) {
      artifactRoots.push({
        root,
        status: "skipped",
        reason: "not a directory",
        filesScanned: 0,
        filesSkipped: [],
      });
      continue;
    }

    const rootScan: ArtifactRootScan = {
      root,
      status: "scanned",
      filesScanned: 0,
      filesSkipped: [],
    };

    let files: string[];
    try {
      files = await listFiles(root);
    } catch (error) {
      artifactRoots.push({
        ...rootScan,
        status: "skipped",
        reason: error instanceof Error ? error.message : "failed to list files",
      });
      continue;
    }

    for (const file of files) {
      let content: Buffer;
      try {
        content = await fs.readFile(file);
      } catch (error) {
        rootScan.filesSkipped.push({
          file,
          reason: error instanceof Error ? error.message : "failed to read file",
        });
        continue;
      }

      if (!isLikelyText(content)) {
        rootScan.filesSkipped.push({ file, reason: "binary or non-UTF-8" });
        continue;
      }

      rootScan.filesScanned += 1;
      const terms = findForbidden(content.toString("utf8"));
      if (terms.length > 0) artifactHits.push({ file, terms });
    }

    artifactRoots.push(rootScan);
  }

  return { artifactHits, artifactRoots };
}

export async function auditBrowserAuthLeakage(
  page: Page,
  label: string,
  testInfo?: TestInfo,
): Promise<AuthLeakageAudit> {
  const browser = await page.evaluate((snapshotLabel) => ({
    label: snapshotLabel,
    localStorage: JSON.stringify(Object.fromEntries(Object.entries(window.localStorage))),
    sessionStorage: JSON.stringify(Object.fromEntries(Object.entries(window.sessionStorage))),
    cookie: document.cookie,
    pageText: document.body.innerText,
  }), label) as BrowserLeakageSnapshot;

  for (const [surface, value] of Object.entries(browser)) {
    if (surface === "label") continue;
    expect(findForbidden(String(value)), `${label} ${surface} forbidden auth leakage`).toEqual([]);
  }

  const artifacts = await scanArtifactRoots(testInfo);
  expect(artifacts.artifactHits, `${label} Playwright artifact forbidden auth leakage`).toEqual([]);
  console.log(
    `[auth-leakage:${label}] artifact roots ` +
      JSON.stringify(artifacts.artifactRoots.map((root) => ({
        root: path.relative(process.cwd(), root.root) || ".",
        status: root.status,
        reason: root.reason,
        filesScanned: root.filesScanned,
        filesSkipped: root.filesSkipped.length,
      }))),
  );

  return { browser, ...artifacts };
}

const authorizationCaptures = new WeakMap<Page, string[]>();

export function enforceNoBearerAuthorization(page: Page): string[] {
  const existing = authorizationCaptures.get(page);
  if (existing) return existing;

  const captures: string[] = [];
  authorizationCaptures.set(page, captures);
  page.on("request", (request) => {
    const url = new URL(request.url());
    if (!url.pathname.startsWith("/api/v1/")) return;
    const authorization = request.headers()["authorization"];
    if (!authorization) return;
    captures.push(`${request.method()} ${url.pathname}: ${authorization}`);
    if (/^bearer\s+/i.test(authorization)) {
      throw new Error(`Browser session request sent bearer Authorization: ${request.method()} ${url.pathname}`);
    }
  });
  return captures;
}

export function expectNoAuthorizationHeaders(page: Page): void {
  expect(authorizationCaptures.get(page) ?? []).toEqual([]);
}
