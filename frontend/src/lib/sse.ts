/**
 * frontend/src/lib/sse.ts
 *
 * Minimal SSE client that works with `fetch()`.
 *
 * Notes:
 * - We avoid `EventSource` so we can attach Authorization headers when needed.
 */

export type SSEEvent = {
  event: string;
  data: unknown;
};

/**
 * HTTP status codes that should NOT be retried.  These indicate
 * client errors that will not resolve by retrying.
 */
const NON_RETRYABLE_STATUS = new Set([400, 401, 403, 404, 405, 409, 410, 422]);

/**
 * Error thrown when the SSE connection fails with a non-retryable
 * HTTP status code.
 */
export class SSEHttpError extends Error {
  readonly status: number;
  readonly retryable: boolean;

  constructor(status: number) {
    super(`SSE failed: ${status}`);
    this.name = "SSEHttpError";
    this.status = status;
    this.retryable = !NON_RETRYABLE_STATUS.has(status);
  }
}

const MAX_EVENT_CHARS = 128 * 1024;

export async function* streamSSE(
  url: string,
  init?: RequestInit & { maxRetries?: number },
): AsyncGenerator<SSEEvent> {
  const maxRetries = init?.maxRetries ?? 3;
  let attempt = 0;

  while (true) {
    try {
      for await (const evt of streamSSEOnce(url, init)) {
        yield evt;
      }
      return; // Stream completed normally
    } catch (err) {
      // Don't retry if the request was intentionally aborted
      if (init?.signal?.aborted) throw err;

      // Don't retry non-retryable HTTP errors (4xx client errors).
      if (err instanceof SSEHttpError && !err.retryable) throw err;

      attempt += 1;
      if (attempt > maxRetries) throw err;

      // Emit a synthetic retry event so consumers can reset accumulated
      // state (e.g. clear partial text) before the next connection yields
      // fresh events.
      yield { event: "sse.retry", data: { attempt } };

      // Exponential backoff: 1s, 2s, 4s — abort-aware so cleanup is instant.
      const delay = Math.min(1000 * 2 ** (attempt - 1), 8000);
      await new Promise<void>((resolve, reject) => {
        const timer = setTimeout(resolve, delay);
        const signal = init?.signal;
        if (signal) {
          if (signal.aborted) {
            clearTimeout(timer);
            reject(signal.reason ?? new DOMException("Aborted", "AbortError"));
            return;
          }
          const onAbort = () => {
            clearTimeout(timer);
            reject(signal.reason ?? new DOMException("Aborted", "AbortError"));
          };
          signal.addEventListener("abort", onAbort, { once: true });
          // Remove the listener when the timer resolves normally to
          // prevent accumulating orphaned listeners across retries.
          const origResolve = resolve;
          resolve = (() => {
            signal.removeEventListener("abort", onAbort);
            origResolve();
          }) as typeof resolve;
        }
      });
    }
  }
}

async function* streamSSEOnce(url: string, init?: RequestInit): AsyncGenerator<SSEEvent> {
  const headers = {
    ...toHeaderObject(init?.headers),
    Accept: "text/event-stream",
  };

  const res = await fetch(url, {
    ...init,
    credentials: init?.credentials ?? "include",
    headers,
  });

  if (!res.ok || !res.body) {
    throw new SSEHttpError(res.status);
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder("utf-8");
  const parser = createSSEParser();

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) {
        for (const evt of parser.feed(decoder.decode(), { flush: true })) {
          yield evt;
        }
        break;
      }

      for (const evt of parser.feed(decoder.decode(value, { stream: true }))) {
        yield evt;
      }
    }
  } finally {
    try {
      await reader.cancel();
    } catch {
      // Ignore cleanup errors on abort/disconnect.
    }
    reader.releaseLock();
  }
}

type ParserFeedOptions = {
  flush?: boolean;
};

export function toHeaderObject(headers?: HeadersInit): Record<string, string> {
  if (!headers) return {};

  if (headers instanceof Headers) {
    const out: Record<string, string> = {};
    headers.forEach((value, key) => {
      out[key] = value;
    });
    return out;
  }

  if (Array.isArray(headers)) {
    const out: Record<string, string> = {};
    for (const [key, value] of headers) {
      out[key] = value;
    }
    return out;
  }

  return { ...headers };
}

function createSSEParser(maxEventChars = MAX_EVENT_CHARS) {
  let buffer = "";
  let eventName = "message";
  let dataLines: string[] = [];
  let eventCharCount = 0;
  let droppingEvent = false;

  function feed(chunk: string, options?: ParserFeedOptions): SSEEvent[] {
    if (chunk) {
      buffer += chunk;
    }

    if (options?.flush && buffer.endsWith("\r")) {
      // Treat a trailing CR as a completed line separator on stream shutdown.
      buffer += "\n";
    }

    const emitted: SSEEvent[] = [];

    while (true) {
      const line = readCompleteLine(buffer);
      if (line === null) {
        break;
      }

      buffer = buffer.slice(line.nextIndex);
      processLine(line.value, emitted);
    }

    if (options?.flush) {
      if (buffer) {
        processLine(buffer, emitted);
        buffer = "";
      }
      flushPendingEvent(emitted);
    }

    return emitted;
  }

  function processLine(line: string, emitted: SSEEvent[]): void {
    if (line.length === 0) {
      flushPendingEvent(emitted);
      return;
    }

    if (line.startsWith(":")) {
      // Comment / heartbeat line.
      return;
    }

    let field = line;
    let value = "";
    const colon = line.indexOf(":");
    if (colon >= 0) {
      field = line.slice(0, colon);
      value = line.slice(colon + 1);
      if (value.startsWith(" ")) {
        value = value.slice(1);
      }
    }

    if (field === "event") {
      if (droppingEvent) {
        return;
      }
      eventName = value || "message";
      return;
    }

    if (field === "data") {
      if (droppingEvent) {
        return;
      }

      eventCharCount += value.length;
      if (eventCharCount > maxEventChars) {
        // Drop pathological events to avoid unbounded memory growth.
        dataLines = [];
        droppingEvent = true;
        return;
      }

      dataLines.push(value);
    }
  }

  function flushPendingEvent(emitted: SSEEvent[]): void {
    if (droppingEvent) {
      eventName = "message";
      dataLines = [];
      eventCharCount = 0;
      droppingEvent = false;
      return;
    }

    if (dataLines.length === 0) {
      eventName = "message";
      eventCharCount = 0;
      return;
    }

    const dataStr = dataLines.join("\n");
    emitted.push({ event: eventName || "message", data: parseEventData(dataStr) });

    eventName = "message";
    dataLines = [];
    eventCharCount = 0;
  }

  return { feed };
}

function readCompleteLine(buffer: string): { value: string; nextIndex: number } | null {
  for (let index = 0; index < buffer.length; index += 1) {
    const char = buffer[index];
    if (char === "\n") {
      return { value: buffer.slice(0, index), nextIndex: index + 1 };
    }
    if (char === "\r") {
      if (index + 1 >= buffer.length) {
        return null;
      }
      const nextIndex = buffer[index + 1] === "\n" ? index + 2 : index + 1;
      return { value: buffer.slice(0, index), nextIndex };
    }
  }

  return null;
}

function parseEventData(data: string): unknown {
  try {
    return JSON.parse(data);
  } catch {
    return data;
  }
}
