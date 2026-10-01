import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, parseNdjson, streamChat } from "./api";

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
