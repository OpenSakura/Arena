import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { SSEHttpError, setSseUnauthorizedHandler, streamSSE, type SSEEvent, type StreamSSEInit } from "./sse";

function buildSSEBody(chunks: string[]): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder();
  return new ReadableStream<Uint8Array>({
    start(controller) {
      for (const chunk of chunks) {
        controller.enqueue(encoder.encode(chunk));
      }
      controller.close();
    },
  });
}

async function collectFromStream(
  url = "http://example.test/sse",
  init?: StreamSSEInit,
): Promise<SSEEvent[]> {
  const events: SSEEvent[] = [];
  for await (const event of streamSSE(url, init)) {
    events.push(event);
  }
  return events;
}

afterEach(() => {
  setSseUnauthorizedHandler(null);
  vi.restoreAllMocks();
  vi.useRealTimers();
});

beforeEach(() => {
  vi.useRealTimers();
});

describe("streamSSE", () => {
  it("parses CRLF events split across chunk boundaries", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(buildSSEBody(["event: run.delta\r\n", "data: {\"side\":\"A\"}\r\n\r\n"]), {
        status: 200,
        headers: { "content-type": "text/event-stream" },
      }),
    );

    const events = await collectFromStream();
    expect(events).toEqual([{ event: "run.delta", data: { side: "A" } }]);
  });

  it("ignores comments and preserves multi-line data payloads", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        buildSSEBody([": keepalive\n\nevent: note\ndata: hello\ndata: world\n\n"]),
        {
          status: 200,
          headers: { "content-type": "text/event-stream" },
        },
      ),
    );

    const events = await collectFromStream();
    expect(events).toEqual([{ event: "note", data: "hello\nworld" }]);
  });

  it("flushes a final event without a trailing blank line", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(buildSSEBody(["event: done\ndata: {\"ok\":true}"]), {
        status: 200,
        headers: { "content-type": "text/event-stream" },
      }),
    );

    const events = await collectFromStream();
    expect(events).toEqual([{ event: "done", data: { ok: true } }]);
  });

  it("supports carriage-return-only separators", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(buildSSEBody(["event: metric\rdata: 123\r\r"]), {
        status: 200,
        headers: { "content-type": "text/event-stream" },
      }),
    );

    const events = await collectFromStream();
    expect(events).toEqual([{ event: "metric", data: 123 }]);
  });

  it("uses message as the default event name", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(buildSSEBody(["data: plain-text\n\n"]), {
        status: 200,
        headers: { "content-type": "text/event-stream" },
      }),
    );

    const events = await collectFromStream();
    expect(events).toEqual([{ event: "message", data: "plain-text" }]);
  });

  it("treats an empty event field as message", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(buildSSEBody(["event:\ndata: 7\n\n"]), {
        status: 200,
        headers: { "content-type": "text/event-stream" },
      }),
    );

    const events = await collectFromStream();
    expect(events).toEqual([{ event: "message", data: 7 }]);
  });

  it("uses credentials and forwards explicit headers passed as a Headers instance", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(buildSSEBody(["data: ok\n\n"]), {
        status: 200,
        headers: { "content-type": "text/event-stream" },
      }),
    );

    const events = await collectFromStream("http://example.test/sse", {
      headers: new Headers([["Authorization", "Bearer token"]]),
    });

    expect(events).toEqual([{ event: "message", data: "ok" }]);

    const requestInit = fetchSpy.mock.calls[0]?.[1] as RequestInit;
    expect(requestInit.credentials).toBe("include");
    expect(requestInit.headers).toMatchObject({
      authorization: "Bearer token",
      Accept: "text/event-stream",
    });
  });

  it("uses cookie credentials without requiring authorization headers", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(buildSSEBody(["data: ok\n\n"]), {
        status: 200,
        headers: { "content-type": "text/event-stream" },
      }),
    );

    await expect(collectFromStream()).resolves.toEqual([{ event: "message", data: "ok" }]);

    const requestInit = fetchSpy.mock.calls[0]?.[1] as RequestInit;
    expect(requestInit.credentials).toBe("include");
    expect(requestInit.headers).toEqual({ Accept: "text/event-stream" });
  });

  it("throws on non-ok responses", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(null, {
        status: 404,
      }),
    );

    await expect(collectFromStream()).rejects.toThrow("SSE failed: 404");
  });

  it("notifies the unauthorized handler on 401 responses", async () => {
    const unauthorizedHandler = vi.fn();
    setSseUnauthorizedHandler(unauthorizedHandler);
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(null, {
        status: 401,
      }),
    );

    await expect(collectFromStream()).rejects.toThrow("SSE failed: 401");
    expect(unauthorizedHandler).toHaveBeenCalledTimes(1);
  });

  it("retries retryable connection failures and resumes event parsing", async () => {
    vi.useFakeTimers();

    const fetchSpy = vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(new Response(null, { status: 503 }))
      .mockResolvedValueOnce(
        new Response(buildSSEBody(['event: done\ndata: {"ok":true}\n\n']), {
          status: 200,
          headers: { "content-type": "text/event-stream" },
        }),
      );

    const promise = collectFromStream("http://example.test/sse", { maxRetries: 1 });
    await vi.advanceTimersByTimeAsync(1000);

    await expect(promise).resolves.toEqual([
      { event: "sse.retry", data: { attempt: 1 } },
      { event: "done", data: { ok: true } },
    ]);
    expect(fetchSpy).toHaveBeenCalledTimes(2);
  });

  it("throws after exhausting max retries", async () => {
    vi.useFakeTimers();

    const error = new SSEHttpError(503);
    vi.spyOn(globalThis, "fetch").mockRejectedValue(error);

    const promise = collectFromStream("http://example.test/sse", { maxRetries: 2 });
    const handledPromise = promise.catch((caught) => {
      expect(caught).toBe(error);
      return caught;
    });

    await vi.advanceTimersByTimeAsync(3000);
    await handledPromise;
  });

  it("stops retrying immediately for aborted connections", async () => {
    const controller = new AbortController();
    const abortError = new DOMException("Aborted", "AbortError");
    vi.spyOn(globalThis, "fetch").mockRejectedValue(abortError);
    controller.abort();

    await expect(
      collectFromStream("http://example.test/sse", { signal: controller.signal }),
    ).rejects.toBe(abortError);
  });

  it("aborts pending retry backoff without reconnecting", async () => {
    const controller = new AbortController();
    const abortError = new DOMException("Aborted", "AbortError");
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockRejectedValue(new SSEHttpError(503));
    const iterator = streamSSE("http://example.test/sse", {
      maxRetries: 3,
      signal: controller.signal,
    });

    await expect(iterator.next()).resolves.toEqual({
      done: false,
      value: { event: "sse.retry", data: { attempt: 1 } },
    });

    const reconnectAttempt = iterator.next();
    controller.abort(abortError);

    await expect(reconnectAttempt).rejects.toBe(abortError);
    expect(fetchSpy).toHaveBeenCalledTimes(1);
  });

  it("drops oversized events and keeps parsing later events", async () => {
    const oversized = "x".repeat(140_000);
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        buildSSEBody([
          `event: note\ndata: ${oversized}\n\n`,
          'event: note\ndata: {"ok":true}\n\n',
        ]),
        {
          status: 200,
          headers: { "content-type": "text/event-stream" },
        },
      ),
    );

    const events = await collectFromStream();
    expect(events).toEqual([{ event: "note", data: { ok: true } }]);
  });

  it("treats malformed JSON payloads as raw strings", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(buildSSEBody(['event: note\ndata: {"bad":\n\n']), {
        status: 200,
        headers: { "content-type": "text/event-stream" },
      }),
    );

    const events = await collectFromStream();
    expect(events).toEqual([{ event: "note", data: '{"bad":' }]);
  });

  it("ignores unknown fields while preserving subsequent data lines", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(buildSSEBody(["id: 1\nretry: 5000\ndata: ok\n\n"]), {
        status: 200,
        headers: { "content-type": "text/event-stream" },
      }),
    );

    const events = await collectFromStream();
    expect(events).toEqual([{ event: "message", data: "ok" }]);
  });

  it("calls getHeaders on each reconnect to refresh auth", async () => {
    vi.useFakeTimers();

    let callCount = 0;
    const getHeaders = () => {
      callCount += 1;
      return { Authorization: `Bearer token-${callCount}` };
    };

    const fetchSpy = vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(new Response(null, { status: 503 }))
      .mockResolvedValueOnce(
        new Response(buildSSEBody(['data: {"ok":true}\n\n']), {
          status: 200,
          headers: { "content-type": "text/event-stream" },
        }),
      );

    const promise = collectFromStream("http://example.test/sse", {
      maxRetries: 1,
      getHeaders,
    });
    await vi.advanceTimersByTimeAsync(1000);

    const events = await promise;
    expect(events).toEqual([
      { event: "sse.retry", data: { attempt: 1 } },
      { event: "message", data: { ok: true } },
    ]);

    expect(callCount).toBe(2);

    const firstInit = fetchSpy.mock.calls[0]?.[1] as RequestInit;
    expect(firstInit.credentials).toBe("include");
    expect((firstInit.headers as Record<string, string>).Authorization).toBe("Bearer token-1");

    const secondInit = fetchSpy.mock.calls[1]?.[1] as RequestInit;
    expect(secondInit.credentials).toBe("include");
    expect((secondInit.headers as Record<string, string>).Authorization).toBe("Bearer token-2");
  });
});
