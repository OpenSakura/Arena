import { createServer } from "node:http";

const rawPort = process.env.MOCK_LLM_PORT ?? "18080";
const port = Number.parseInt(rawPort, 10);

if (!Number.isFinite(port) || port <= 0) {
  throw new Error(`Invalid MOCK_LLM_PORT: ${rawPort}`);
}

function jsonResponse(res, statusCode, payload) {
  res.statusCode = statusCode;
  res.setHeader("content-type", "application/json; charset=utf-8");
  res.end(JSON.stringify(payload));
}

function readJsonBody(req) {
  return new Promise((resolve, reject) => {
    const chunks = [];

    req.on("data", (chunk) => {
      chunks.push(chunk);
    });
    req.on("error", (error) => {
      reject(error);
    });
    req.on("end", () => {
      const bodyText = Buffer.concat(chunks).toString("utf-8").trim();
      if (!bodyText) {
        resolve({});
        return;
      }

      try {
        const parsed = JSON.parse(bodyText);
        if (!parsed || typeof parsed !== "object") {
          reject(new Error("Request JSON must be an object"));
          return;
        }
        resolve(parsed);
      } catch (error) {
        reject(error);
      }
    });
  });
}

function outputForModel(modelName) {
  const normalized = String(modelName || "").toLowerCase();
  if (normalized.includes("playwright-live-model-a")) {
    return "E2E Alpha translation from mock gateway.";
  }
  if (normalized.includes("playwright-live-model-b")) {
    return "E2E Beta translation from mock gateway.";
  }
  return `E2E generic translation for ${modelName || "unknown-model"}.`;
}

function splitInTwo(text) {
  const pivot = Math.max(1, Math.floor(text.length / 2));
  return [text.slice(0, pivot), text.slice(pivot)];
}

function writeSSEData(res, payload) {
  res.write(`data: ${payload}\n\n`);
}

const server = createServer(async (req, res) => {
  if (req.method === "GET" && req.url === "/healthz") {
    jsonResponse(res, 200, { ok: true });
    return;
  }

  if (req.method !== "POST" || req.url !== "/v1/chat/completions") {
    jsonResponse(res, 404, { detail: "Not found" });
    return;
  }

  let payload;
  try {
    payload = await readJsonBody(req);
  } catch (error) {
    jsonResponse(res, 400, {
      detail: error instanceof Error ? error.message : "Invalid JSON body",
    });
    return;
  }

  const modelName = typeof payload.model === "string" ? payload.model : "unknown-model";
  const outputText = outputForModel(modelName);

  if (payload.stream !== true) {
    jsonResponse(res, 200, {
      id: "chatcmpl-mock",
      object: "chat.completion",
      model: modelName,
      choices: [
        {
          index: 0,
          finish_reason: "stop",
          message: {
            role: "assistant",
            content: outputText,
          },
        },
      ],
      usage: {
        prompt_tokens: 8,
        completion_tokens: 8,
        total_tokens: 16,
      },
    });
    return;
  }

  const [chunkA, chunkB] = splitInTwo(outputText);

  res.statusCode = 200;
  res.setHeader("content-type", "text/event-stream; charset=utf-8");
  res.setHeader("cache-control", "no-cache");
  res.setHeader("connection", "keep-alive");

  writeSSEData(res, JSON.stringify({ choices: [{ delta: { content: chunkA } }] }));
  writeSSEData(res, JSON.stringify({ choices: [{ delta: { content: chunkB } }] }));
  writeSSEData(
    res,
    JSON.stringify({
      choices: [{ delta: {}, finish_reason: "stop" }],
      usage: {
        prompt_tokens: 8,
        completion_tokens: 8,
        total_tokens: 16,
      },
    }),
  );
  writeSSEData(res, "[DONE]");
  res.end();
});

server.listen(port, "127.0.0.1", () => {
  // eslint-disable-next-line no-console
  console.log(`Mock OpenAI server listening on http://127.0.0.1:${port}`);
});

function shutdown(signal) {
  server.close(() => {
    // eslint-disable-next-line no-console
    console.log(`Mock OpenAI server stopped after ${signal}`);
    process.exit(0);
  });
}

process.on("SIGINT", () => shutdown("SIGINT"));
process.on("SIGTERM", () => shutdown("SIGTERM"));
