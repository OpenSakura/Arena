import { afterEach, describe, expect, it, vi } from "vitest";

import { apiDelete, apiGet, apiPatch, apiPost, apiPut, getApiCsrfToken, setApiCsrfToken } from "./api";

afterEach(() => {
  setApiCsrfToken(null);
  vi.restoreAllMocks();
});

describe("api helpers", () => {
  it("apiGet sends same-origin credentials with default headers", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ ok: true }), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    );

    setApiCsrfToken("csrf-token-1");

    await expect(apiGet("/health")).resolves.toEqual({ ok: true });

    expect(fetchSpy).toHaveBeenCalledWith(
      "/api/v1/health",
      expect.objectContaining({
        method: "GET",
        credentials: "include",
        cache: "no-store",
        body: undefined,
        headers: { Accept: "application/json" },
      }),
    );
    const init = fetchSpy.mock.calls[0]?.[1] as RequestInit;
    expect(new Headers(init.headers as HeadersInit).get("x-csrf-token")).toBeNull();
  });

  it("apiGet normalizes paths without leading slash", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ ok: true }), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    );

    await apiGet("health");

    expect(fetchSpy).toHaveBeenCalledWith(
      "/api/v1/health",
      expect.anything(),
    );
  });

  it("keeps the CSRF token in module memory", () => {
    setApiCsrfToken("csrf-token-2");

    expect(getApiCsrfToken()).toBe("csrf-token-2");
  });

  it("apiPost merges caller headers, forces credentials, and serializes body with CSRF", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ created: true }), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    );

    setApiCsrfToken("csrf-token-3");

    await expect(
      apiPost("/votes", { winner: "A" }, {
        credentials: "omit",
        headers: { Authorization: "Bearer service-token", "X-Request-ID": "req-1" },
      }),
    ).resolves.toEqual({ created: true });

    expect(fetchSpy).toHaveBeenCalledWith(
      "/api/v1/votes",
      expect.objectContaining({
        method: "POST",
        credentials: "include",
        cache: "no-store",
        body: JSON.stringify({ winner: "A" }),
        headers: {
          Authorization: "Bearer service-token",
          "X-Request-ID": "req-1",
          Accept: "application/json",
          "Content-Type": "application/json",
          "X-CSRF-Token": "csrf-token-3",
        },
      }),
    );
  });

  it("apiPost uses cookie and CSRF auth without adding Authorization for browser calls", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ created: true }), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    );

    setApiCsrfToken("csrf-token-browser");

    await expect(apiPost("/battles/battle-1/vote", { winner: "A" })).resolves.toEqual({ created: true });

    const init = fetchSpy.mock.calls[0]?.[1] as RequestInit;
    const sentHeaders = new Headers(init.headers as HeadersInit);
    expect(init.credentials).toBe("include");
    expect(sentHeaders.get("x-csrf-token")).toBe("csrf-token-browser");
    expect(sentHeaders.get("authorization")).toBeNull();
  });

  it("apiPost preserves explicit headers from Headers instances", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ created: true }), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    );

    setApiCsrfToken("csrf-token-4");

    const callerHeaders = new Headers({ Authorization: "Bearer service-token" });
    await expect(apiPost("/votes", { winner: "A" }, { headers: callerHeaders })).resolves.toEqual({
      created: true,
    });

    const [, init] = fetchSpy.mock.calls[0] as [string, RequestInit];
    const sentHeaders = new Headers(init.headers as HeadersInit);
    expect(sentHeaders.get("authorization")).toBe("Bearer service-token");
    expect(sentHeaders.get("accept")).toBe("application/json");
    expect(sentHeaders.get("content-type")).toBe("application/json");
    expect(sentHeaders.get("x-csrf-token")).toBe("csrf-token-4");
  });

  it("apiPut sends CSRF and keeps leading slash paths intact", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ ok: true }), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    );

    setApiCsrfToken("csrf-token-5");

    await expect(apiPut("/tasks/123", { enabled: true })).resolves.toEqual({ ok: true });

    expect(fetchSpy).toHaveBeenCalledWith(
      "/api/v1/tasks/123",
      expect.objectContaining({
        method: "PUT",
        credentials: "include",
        headers: expect.objectContaining({ "X-CSRF-Token": "csrf-token-5" }),
      }),
    );
  });

  it("apiPatch sends CSRF on unsafe requests", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ ok: true }), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    );

    setApiCsrfToken("csrf-token-6");

    await expect(apiPatch("/tasks/123", { enabled: false })).resolves.toEqual({ ok: true });

    expect(fetchSpy).toHaveBeenCalledWith(
      "/api/v1/tasks/123",
      expect.objectContaining({
        method: "PATCH",
        credentials: "include",
        headers: expect.objectContaining({ "X-CSRF-Token": "csrf-token-6" }),
      }),
    );
  });

  it("apiDelete handles empty success responses with credentials and CSRF", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(null, {
        status: 204,
      }),
    );

    setApiCsrfToken("csrf-token-7");

    await expect(apiDelete("/admin/tasks/1")).resolves.toBeNull();

    expect(fetchSpy).toHaveBeenCalledWith(
      "/api/v1/admin/tasks/1",
      expect.objectContaining({
        method: "DELETE",
        credentials: "include",
        body: undefined,
        headers: { Accept: "application/json", "X-CSRF-Token": "csrf-token-7" },
      }),
    );
  });

  it("does not overwrite explicit CSRF headers", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ ok: true }), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    );

    setApiCsrfToken("current-token");

    await apiPost("/votes", {}, { headers: { "X-CSRF-Token": "explicit-token" } });

    const init = fetchSpy.mock.calls[0]?.[1] as RequestInit;
    expect(new Headers(init.headers as HeadersInit).get("x-csrf-token")).toBe("explicit-token");
  });

  it("includes JSON detail string in error messages", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ detail: "too many requests" }), {
        status: 429,
        headers: { "content-type": "application/json" },
      }),
    );

    await expect(apiGet("/battles")).rejects.toThrow(
      "GET /battles failed: 429 - too many requests",
    );
  });

  it("falls back to stringified JSON when detail is missing", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ error_code: 17 }), {
        status: 400,
        headers: { "content-type": "application/json" },
      }),
    );

    await expect(apiPost("/votes", { winner: "A" })).rejects.toThrow(
      'POST /votes failed: 400 - {"error_code":17}',
    );
  });

  it("includes text response body for non-JSON failures", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response("service unavailable", {
        status: 503,
        headers: { "content-type": "text/plain" },
      }),
    );

    await expect(apiGet("/leaderboard")).rejects.toThrow(
      "GET /leaderboard failed: 503 - service unavailable",
    );
  });

  it("omits error suffix when response body is empty", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response("", {
        status: 500,
        headers: { "content-type": "text/plain" },
      }),
    );

    await expect(apiPut("/tasks/1", { enabled: false })).rejects.toThrow(
      "PUT /tasks/1 failed: 500",
    );
  });

  it("handles invalid JSON error bodies without a detail suffix", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response("not-json", {
        status: 502,
        headers: { "content-type": "application/json" },
      }),
    );

    await expect(apiGet("/battles")).rejects.toThrow("GET /battles failed: 502");
  });

  it("omits body for undefined POST payloads", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ ok: true }), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    );

    setApiCsrfToken("csrf-token-8");

    await expect(apiPost("/votes", undefined)).resolves.toEqual({ ok: true });

    expect(fetchSpy).toHaveBeenCalledWith(
      "/api/v1/votes",
      expect.objectContaining({
        method: "POST",
        credentials: "include",
        body: undefined,
        headers: expect.objectContaining({ "X-CSRF-Token": "csrf-token-8" }),
      }),
    );
  });

  it("parses validation error arrays from FastAPI into readable strings", async () => {
    const errorBody = {
      detail: [
        { loc: ["body", "winner"], msg: "field required", type: "value_error.missing" },
        { loc: ["query", "page"], msg: "must be an integer", type: "type_error.integer" },
      ],
    };
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify(errorBody), {
        status: 422,
        headers: { "content-type": "application/json" },
      }),
    );

    await expect(apiPost("/some-endpoint", {})).rejects.toThrow(
      "POST /some-endpoint failed: 422 - body.winner: field required, query.page: must be an integer",
    );
  });
});
