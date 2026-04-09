import { afterEach, describe, expect, it, vi } from "vitest";

import { streamSSE, type SSEEvent } from "./sse";

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
  init?: RequestInit,
): Promise<SSEEvent[]> {
  const events: SSEEvent[] = [];
  for await (const event of streamSSE(url, init)) {
    events.push(event);
  }
  return events;
}

afterEach(() => {
  vi.restoreAllMocks();
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

  it("forwards headers passed as a Headers instance", async () => {
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
    expect(requestInit.headers).toMatchObject({
      authorization: "Bearer token",
      Accept: "text/event-stream",
    });
  });

  it("throws on non-ok responses", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(null, {
        status: 404,
      }),
    );

    await expect(collectFromStream()).rejects.toThrow("SSE failed: 404");
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
});
