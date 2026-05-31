/**
 * frontend/src/lib/api.ts
 *
 * Thin client for the backend REST API.
 *
 * All requests target same-origin `/api/v1/...` paths so no absolute backend
 * URL or environment variable is needed. Human browser auth is carried by the
 * backend-owned same-origin session cookie, with unsafe requests protected by
 * the in-memory CSRF token supplied by the auth session bootstrap.
 */

import { toHeaderObject } from "@/lib/sse";

const API_PREFIX = "/api/v1";

type ApiMethod = "GET" | "POST" | "PUT" | "PATCH" | "DELETE";

const UNSAFE_METHODS = new Set<ApiMethod>(["POST", "PUT", "PATCH", "DELETE"]);

let apiCsrfToken: string | null = null;
let apiCsrfHeaderName: string = "X-CSRF-Token";
let apiUnauthorizedHandler: (() => void) | null = null;

export class ApiHttpError extends Error {
  readonly status: number;
  readonly method: ApiMethod;
  readonly path: string;
  readonly detail: string | null;

  constructor(method: ApiMethod, path: string, status: number, detail: string | null) {
    const suffix = detail ? ` - ${detail}` : "";
    super(`${method} ${path} failed: ${status}${suffix}`);
    this.name = "ApiHttpError";
    this.status = status;
    this.method = method;
    this.path = path;
    this.detail = detail;
  }
}

export function getApiPrefix(): string {
  return API_PREFIX;
}

export function setApiCsrfToken(token: string | null | undefined): void {
  apiCsrfToken = token ?? null;
}

export function getApiCsrfToken(): string | null {
  return apiCsrfToken;
}

export function setApiCsrfHeaderName(name: string | null | undefined): void {
  const normalizedName = name?.trim();
  apiCsrfHeaderName = normalizedName || "X-CSRF-Token";
}

export function getApiCsrfHeaderName(): string {
  return apiCsrfHeaderName;
}

export function setApiUnauthorizedHandler(handler: (() => void) | null | undefined): void {
  apiUnauthorizedHandler = handler ?? null;
}

export function isApiUnauthorizedError(error: unknown): boolean {
  return error instanceof ApiHttpError && error.status === 401;
}

async function readErrorDetail(res: Response): Promise<string | null> {
  const ct = res.headers.get("content-type") ?? "";
  try {
    if (ct.includes("application/json")) {
      const data: unknown = await res.json();
      if (data && typeof data === "object") {
        const detail = (data as Record<string, unknown>).detail;
        if (typeof detail === "string") return detail;
        if (Array.isArray(detail)) {
          return detail
            .map((err) => {
              if (typeof err === "object" && err !== null) {
                const loc = Array.isArray(err.loc) ? err.loc.join(".") : "";
                const msg = typeof err.msg === "string" ? err.msg : "";
                return loc ? `${loc}: ${msg}` : msg;
              }
              return JSON.stringify(err);
            })
            .filter(Boolean)
            .join(", ");
        }
      }
      return JSON.stringify(data);
    }

    const text = await res.text();
    return text ? text : null;
  } catch {
    return null;
  }
}

function isFormDataBody(body: unknown): body is FormData {
  return typeof FormData !== "undefined" && body instanceof FormData;
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

function hasHeader(headers: Record<string, string>, name: string): boolean {
  const normalizedName = name.toLowerCase();
  return Object.keys(headers).some((key) => key.toLowerCase() === normalizedName);
}

function setDefaultHeader(headers: Record<string, string>, name: string, value: string): void {
  if (!hasHeader(headers, name)) {
    headers[name] = value;
  }
}

function buildRequestHeaders(
  method: ApiMethod,
  initHeaders: HeadersInit | undefined,
  hasJsonBody: boolean,
): Record<string, string> {
  const mergedHeaders = {
    ...toHeaderObject(initHeaders),
  };

  setDefaultHeader(mergedHeaders, "Accept", "application/json");
  if (hasJsonBody) {
    setDefaultHeader(mergedHeaders, "Content-Type", "application/json");
  }

  if (UNSAFE_METHODS.has(method) && apiCsrfToken && !hasHeader(mergedHeaders, apiCsrfHeaderName)) {
    mergedHeaders[apiCsrfHeaderName] = apiCsrfToken;
  }

  return mergedHeaders;
}

async function apiRequest<T = unknown>(
  method: ApiMethod,
  path: string,
  body?: unknown,
  init?: RequestInit,
): Promise<T> {
  const normalizedPath = path.startsWith("/") ? path : `/${path}`;
  const url = `${API_PREFIX}${normalizedPath}`;
  const hasJsonBody =
    method !== "GET" && method !== "DELETE" && body !== undefined && !isFormDataBody(body);
  const mergedHeaders = buildRequestHeaders(method, init?.headers, hasJsonBody);

  const res = await fetch(url, {
    ...init,
    method,
    credentials: "include",
    headers: mergedHeaders,
    body:
      method === "GET" || method === "DELETE"
        ? undefined
        : body === undefined
          ? undefined
          : isFormDataBody(body)
            ? body
            : JSON.stringify(body),
    cache: "no-store",
  });

  if (!res.ok) {
    const detail = await readErrorDetail(res);
    const error = new ApiHttpError(method, normalizedPath, res.status, detail);
    if (res.status === 401) {
      apiUnauthorizedHandler?.();
    }
    throw error;
  }

  return readSuccessBody(res) as Promise<T>;
}

export async function apiGet<T = unknown>(path: string, init?: RequestInit): Promise<T> {
  return apiRequest<T>("GET", path, undefined, init);
}

export async function apiPost<T = unknown>(
  path: string,
  body: unknown,
  init?: RequestInit,
): Promise<T> {
  return apiRequest<T>("POST", path, body, init);
}

export async function apiPut<T = unknown>(
  path: string,
  body: unknown,
  init?: RequestInit,
): Promise<T> {
  return apiRequest<T>("PUT", path, body, init);
}

export async function apiPatch<T = unknown>(
  path: string,
  body: unknown,
  init?: RequestInit,
): Promise<T> {
  return apiRequest<T>("PATCH", path, body, init);
}

export async function apiDelete<T = unknown>(path: string, init?: RequestInit): Promise<T> {
  return apiRequest<T>("DELETE", path, undefined, init);
}
