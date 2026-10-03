import type { ChatEvent, ChatMessage } from "./types";

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly retryAfter?: number,
  ) {
    super(message);
  }
}

async function apiFetch(path: string, init?: RequestInit): Promise<Response> {
  const response = await fetch(path, { credentials: "same-origin", ...init });
  if (!response.ok) {
    const data = await response.json().catch(() => null);
    const retryAfter = Number(response.headers.get("Retry-After")) || undefined;
    const detail = typeof data?.detail === "string" ? data.detail : "請求失敗，請稍後再試。";
    throw new ApiError(
      retryAfter && response.status === 429 ? `${detail}，請於 ${retryAfter} 秒後重試。` : detail,
      response.status,
      retryAfter,
    );
  }
  return response;
}

export async function getSession(): Promise<{ authenticated: boolean; authEnabled: boolean }> {
  const response = await apiFetch("/api/v1/auth/session");
  return response.json();
}

export async function login(apiKey: string): Promise<void> {
  await apiFetch("/api/v1/auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ apiKey }),
  });
}

export async function logout(): Promise<void> {
  await apiFetch("/api/v1/auth/logout", { method: "POST" });
}

export async function getMessages(): Promise<ChatMessage[]> {
  const response = await apiFetch("/api/v1/conversations/current/messages");
  const data = await response.json();
  return data.items.map((item: Pick<ChatMessage, "role" | "content">, index: number) => ({
    ...item,
    id: `history-${index}`,
  }));
}

export async function clearMessages(): Promise<void> {
  const response = await apiFetch("/api/v1/conversations/current/messages", { method: "DELETE" });
  if (response.status !== 204) throw new ApiError("尚未確認對話已清除，請稍後重試", response.status);
}

export type SyncJob = {
  jobId: string;
  status: "queued" | "running" | "succeeded" | "failed";
  message?: string;
};

export async function uploadManual(file: File): Promise<SyncJob> {
  const body = new FormData();
  body.append("file", file);
  const response = await apiFetch("/api/v1/admin/manuals", { method: "POST", body });
  return response.json();
}

export async function getSyncJob(jobId: string): Promise<SyncJob> {
  const response = await apiFetch(`/api/v1/admin/jobs/${encodeURIComponent(jobId)}`);
  return response.json();
}

function decodeEvent(value: unknown): ChatEvent {
  if (!value || typeof value !== "object") throw new Error("串流事件格式無效");
  const event = value as Record<string, unknown>;
  const string = (key: string) => typeof event[key] === "string";
  let valid = false;
  switch (event.type) {
    case "status": valid = ["retrieving", "reranking", "generating"].includes(String(event.stage)) && string("message"); break;
    case "delta": valid = string("text"); break;
    case "sources": valid = Array.isArray(event.items) && event.items.every((item) => item && typeof item === "object" && typeof item.label === "string"); break;
    case "metadata": valid = typeof event.elapsedMs === "number" && Number.isInteger(event.elapsedMs) && event.elapsedMs >= 0 && ["rag", "direct", "cache", "guardrail"].includes(String(event.responseSource)) && typeof event.cacheHit === "boolean"; break;
    case "done": valid = true; break;
    case "error": valid = event.code === "internal_error" && string("message"); break;
  }
  if (!valid) throw new Error("串流事件格式無效");
  return value as ChatEvent;
}

export async function* parseNdjson(
  stream: ReadableStream<Uint8Array>,
  signal?: AbortSignal,
): AsyncGenerator<ChatEvent> {
  const reader = stream.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  const cancel = () => { void reader.cancel().catch(() => undefined); };
  signal?.addEventListener("abort", cancel, { once: true });
  try {
    while (true) {
      if (signal?.aborted) throw new DOMException("Aborted", "AbortError");
      const { value, done } = await reader.read();
      if (signal?.aborted) throw new DOMException("Aborted", "AbortError");
      buffer += decoder.decode(value, { stream: !done });
      const lines = buffer.split("\n");
      buffer = lines.pop() ?? "";
      for (const line of lines) {
        if (line.trim()) yield decodeEvent(JSON.parse(line));
      }
      if (done) break;
    }
    if (buffer.trim()) throw new Error("串流回應不完整");
  } finally {
    signal?.removeEventListener("abort", cancel);
    await reader.cancel().catch(() => undefined);
    reader.releaseLock();
  }
}

export async function* streamChat(message: string, signal: AbortSignal): AsyncGenerator<ChatEvent> {
  const response = await apiFetch("/api/v1/chat/stream", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message }),
    signal,
  });
  if (!response.body) throw new Error("瀏覽器不支援串流回應");
  let terminal = false;
  for await (const event of parseNdjson(response.body, signal)) {
    if (terminal) throw new Error("串流完成後收到額外事件");
    terminal = event.type === "done" || event.type === "error";
    yield event;
  }
  if (!terminal) throw new Error("串流回應不完整");
}
