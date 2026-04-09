import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { apiDelete, apiGet, apiPost, apiPut, getBackendBaseUrl } from "./api";

afterEach(() => {
  vi.restoreAllMocks();
  delete process.env.NEXT_PUBLIC_BACKEND_URL;
});

describe("getBackendBaseUrl", () => {
  it("throws when NEXT_PUBLIC_BACKEND_URL is missing", () => {
    delete process.env.NEXT_PUBLIC_BACKEND_URL;

    expect(() => getBackendBaseUrl()).toThrow("NEXT_PUBLIC_BACKEND_URL is not set");
  });

  it("normalizes trailing slash", () => {
    process.env.NEXT_PUBLIC_BACKEND_URL = "http://backend.test/";

    expect(getBackendBaseUrl()).toBe("http://backend.test");
  });
});

describe("api helpers", () => {
  beforeEach(() => {
    process.env.NEXT_PUBLIC_BACKEND_URL = "http://backend.test/";
  });

  it("apiGet sends default headers and credentials", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ ok: true }), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    );

    await expect(apiGet("health")).resolves.toEqual({ ok: true });

    expect(fetchSpy).toHaveBeenCalledWith(
      "http://backend.test/health",
      expect.objectContaining({
        method: "GET",
        credentials: "include",
        cache: "no-store",
        body: undefined,
        headers: { Accept: "application/json" },
      }),
    );
  });

  it("apiPost merges caller headers and serializes body", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ created: true }), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    );

    await expect(
      apiPost("votes", { winner: "A" }, {
        credentials: "omit",
        headers: { Authorization: "Bearer token" },
      }),
    ).resolves.toEqual({ created: true });

    expect(fetchSpy).toHaveBeenCalledWith(
      "http://backend.test/votes",
      expect.objectContaining({
        method: "POST",
        credentials: "omit",
        cache: "no-store",
        body: JSON.stringify({ winner: "A" }),
        headers: {
          Authorization: "Bearer token",
          Accept: "application/json",
          "Content-Type": "application/json",
        },
      }),
    );
  });

  it("apiPost preserves auth header from Headers instances", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ created: true }), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    );

    const callerHeaders = new Headers({ Authorization: "Bearer token" });
    await expect(apiPost("votes", { winner: "A" }, { headers: callerHeaders })).resolves.toEqual({
      created: true,
    });

    const [, init] = fetchSpy.mock.calls[0] as [string, RequestInit];
    const sentHeaders = new Headers(init.headers as HeadersInit);
    expect(sentHeaders.get("authorization")).toBe("Bearer token");
    expect(sentHeaders.get("accept")).toBe("application/json");
    expect(sentHeaders.get("content-type")).toBe("application/json");
  });

  it("apiPut keeps leading slash paths intact", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ ok: true }), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    );

    await expect(apiPut("/tasks/123", { enabled: true })).resolves.toEqual({ ok: true });

    expect(fetchSpy).toHaveBeenCalledWith(
      "http://backend.test/tasks/123",
      expect.objectContaining({
        method: "PUT",
      }),
    );
  });

  it("apiDelete handles empty success responses", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(null, {
        status: 204,
      }),
    );

    await expect(apiDelete("/admin/tasks/1")).resolves.toBeNull();

    expect(fetchSpy).toHaveBeenCalledWith(
      "http://backend.test/admin/tasks/1",
      expect.objectContaining({
        method: "DELETE",
        body: undefined,
        headers: { Accept: "application/json" },
      }),
    );
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

    await expect(apiPost("/votes", undefined)).resolves.toEqual({ ok: true });

    expect(fetchSpy).toHaveBeenCalledWith(
      "http://backend.test/votes",
      expect.objectContaining({
        method: "POST",
        body: undefined,
      }),
    );
  });
});
