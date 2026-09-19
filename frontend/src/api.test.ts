import { describe, expect, it } from "vitest";
import { parseNdjson } from "./api";

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
