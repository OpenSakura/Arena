/**
 * frontend/src/lib/api.ts
 *
 * Thin client for the backend REST API.
 *
 * Notes:
 * - This file centralizes request wiring so pages/components stay clean.
 * - Attach OIDC access token when available (caller provides Authorization).
 * - Use `credentials: "include"` so anonymous cookies (arena_anon_id) persist.
 */

import { toHeaderObject } from "@/lib/sse";

export function getBackendBaseUrl(): string {
  // Prefer an internal URL for server-side rendering (Next.js server
  // components running behind a reverse proxy may not be able to reach the
  // public URL).  Falls back to the public URL for client-side code.
  const base =
    (typeof window === "undefined"
      ? process.env.BACKEND_INTERNAL_URL
      : undefined) ?? process.env.NEXT_PUBLIC_BACKEND_URL;
  if (!base) {
    throw new Error("NEXT_PUBLIC_BACKEND_URL is not set");
  }
  return base.replace(/\/$/, "");
}

async function readErrorDetail(res: Response): Promise<string | null> {
  const ct = res.headers.get("content-type") ?? "";
  try {
    if (ct.includes("application/json")) {
      const data = (await res.json()) as unknown;
      if (data && typeof data === "object") {
        const detail = (data as Record<string, unknown>).detail;
        if (typeof detail === "string") return detail;
      }
      return JSON.stringify(data);
    }

    const text = await res.text();
    return text ? text : null;
  } catch {
    return null;
  }
}

async function readSuccessBody(res: Response): Promise<unknown> {
  if (res.status === 204 || res.status === 205) {
    return null;
  }

  const contentLength = res.headers.get("content-length");
  if (contentLength === "0") {
    return null;
  }

  const ct = res.headers.get("content-type") ?? "";
  if (ct.includes("application/json")) {
    try {
      return await res.json();
    } catch (err) {
      console.warn("Failed to parse JSON response body:", err);
      return null;
    }
  }

  const text = await res.text();
  return text ? text : null;
}

async function apiRequest(
  method: "GET" | "POST" | "PUT" | "DELETE",
  path: string,
  body?: unknown,
  init?: RequestInit,
): Promise<unknown> {
  const url = `${getBackendBaseUrl()}${path.startsWith("/") ? path : `/${path}`}`;
  const mergedHeaders = {
    ...toHeaderObject(init?.headers),
    Accept: "application/json",
    ...((method === "GET" || method === "DELETE") ? {} : { "Content-Type": "application/json" }),
  };

  // Spread `init` first so our explicit properties always win.
  // Previously `...init` came after `method`, allowing callers to
  // accidentally override the HTTP method via init.
  const res = await fetch(url, {
    ...init,
    method,
    credentials: init?.credentials ?? "include",
    headers: mergedHeaders,
    body: method === "GET" || method === "DELETE" ? undefined : body !== undefined ? JSON.stringify(body) : undefined,
    cache: "no-store",
  });

  if (!res.ok) {
    const detail = await readErrorDetail(res);
    const suffix = detail ? ` - ${detail}` : "";
    throw new Error(`${method} ${path} failed: ${res.status}${suffix}`);
  }

  return readSuccessBody(res);
}

export async function apiGet(path: string, init?: RequestInit): Promise<unknown> {
  return apiRequest("GET", path, undefined, init);
}

export async function apiPost(
  path: string,
  body: unknown,
  init?: RequestInit,
): Promise<unknown> {
  return apiRequest("POST", path, body, init);
}

export async function apiPut(
  path: string,
  body: unknown,
  init?: RequestInit,
): Promise<unknown> {
  return apiRequest("PUT", path, body, init);
}

export async function apiDelete(path: string, init?: RequestInit): Promise<unknown> {
  return apiRequest("DELETE", path, undefined, init);
}
