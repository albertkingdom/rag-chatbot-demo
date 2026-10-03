import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, clearMessages, parseNdjson, streamChat } from "./api";

function byteStream(chunks: string[]): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder();
  return new ReadableStream({
    start(controller) {
      chunks.forEach((chunk) => controller.enqueue(encoder.encode(chunk)));
      controller.close();
    },
  });
}

describe("parseNdjson", () => {
  it("buffers a JSON line split across network chunks", async () => {
    const events = [];
    for await (const event of parseNdjson(byteStream([
      '{"type":"del',
      'ta","text":"回答"}\n{"type":"done"}\n',
    ]))) events.push(event);

    expect(events).toEqual([
      { type: "delta", text: "回答" },
      { type: "done" },
    ]);
  });

  it("parses multiple lines from one chunk", async () => {
    const events = [];
    for await (const event of parseNdjson(byteStream([
      '{"type":"status","stage":"retrieving","message":"檢索中"}\n' +
      '{"type":"delta","text":"A"}\n',
    ]))) events.push(event);

    expect(events).toHaveLength(2);
  });

  it("rejects a truncated final line", async () => {
    const consume = async () => {
      for await (const _event of parseNdjson(byteStream(['{"type":"done"}']))) {
        // no-op
      }
    };
    await expect(consume()).rejects.toThrow("串流回應不完整");
  });

  it("rejects malformed JSON instead of silently dropping an event", async () => {
    const consume = async () => {
      for await (const _event of parseNdjson(byteStream(['{"type":"delta",oops}\n']))) {
        // no-op
      }
    };
    await expect(consume()).rejects.toThrow();
  });
});


afterEach(() => vi.unstubAllGlobals());

describe("stream completion and HTTP errors", () => {
  async function consume(chunks: string[]) {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(byteStream(chunks))));
    const events = [];
    for await (const event of streamChat("問題", new AbortController().signal)) events.push(event);
    return events;
  }
  it("requires a terminal event even when the last line has a newline", async () => {
    await expect(consume(['{"type":"delta","text":"partial"}\n'])).rejects.toThrow("串流回應不完整");
  });
  it("rejects duplicate completion events", async () => {
    await expect(consume(['{"type":"done"}\n{"type":"done"}\n'])).rejects.toThrow("額外事件");
  });
  it("accepts one final done event", async () => {
    await expect(consume(['{"type":"delta","text":"answer"}\n{"type":"done"}\n'])).resolves.toHaveLength(2);
  });
  it("preserves 429 Retry-After for the caller", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: "Rate limit exceeded" }), { status: 429, headers: { "Retry-After": "12" } })));
    try {
      for await (const event of streamChat("問題", new AbortController().signal)) void event;
      throw new Error("expected rejection");
    } catch (error) {
      expect(error).toBeInstanceOf(ApiError);
      expect(error).toMatchObject({ status: 429, retryAfter: 12 });
    }
  });
});


describe("remaining parser/client boundaries", () => {
  it("decodes Unicode split inside a UTF-8 character", async () => {
    const bytes = new TextEncoder().encode('{"type":"delta","text":"碳"}\n');
    const start = bytes.indexOf(0xe7);
    const stream = new ReadableStream<Uint8Array>({ start(controller) { controller.enqueue(bytes.slice(0, start + 1)); controller.enqueue(bytes.slice(start + 1)); controller.close(); } });
    const events = []; for await (const event of parseNdjson(stream)) events.push(event);
    expect(events).toEqual([{ type: "delta", text: "碳" }]);
  });
  it.each(['{"type":"unknown"}', '{"type":"delta","text":1}', '{"type":"metadata","elapsedMs":-1,"cacheHit":false,"responseSource":"rag"}'])("rejects unsupported event schema %s", async (line) => {
    async function consume() { for await (const event of parseNdjson(byteStream([line + "\n"]))) void event; }
    await expect(consume()).rejects.toThrow("串流事件格式無效");
  });
  it("aborts a pending read and cancels the response body", async () => {
    const cancel = vi.fn(); const controller = new AbortController();
    const events = parseNdjson(new ReadableStream<Uint8Array>({ cancel }), controller.signal);
    const pending = events.next(); controller.abort();
    await expect(pending).rejects.toMatchObject({ name: "AbortError" });
    expect(cancel).toHaveBeenCalledTimes(1);
  });
  it("returns 401 to the caller without retrying", async () => {
    const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: "Expired session", code: "unauthorized" }), { status: 401 }));
    vi.stubGlobal("fetch", fetch);
    async function consume() { for await (const event of streamChat("q", new AbortController().signal)) void event; }
    await expect(consume()).rejects.toMatchObject({ status: 401 });
    expect(fetch).toHaveBeenCalledTimes(1);
  });
  it("does not accept a clear response other than 204", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("{}", { status: 200 })));
    await expect(clearMessages()).rejects.toThrow("尚未確認對話已清除");
  });
});
