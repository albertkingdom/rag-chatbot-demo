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
    throw new ApiError(
      data?.detail ?? "請求失敗，請稍後再試。",
      response.status,
      Number(response.headers.get("Retry-After")) || undefined,
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
  await apiFetch("/api/v1/conversations/current/messages", { method: "DELETE" });
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

export async function* parseNdjson(
  stream: ReadableStream<Uint8Array>,
): AsyncGenerator<ChatEvent> {
  const reader = stream.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    while (true) {
      const { value, done } = await reader.read();
      buffer += decoder.decode(value, { stream: !done });
      const lines = buffer.split("\n");
      buffer = lines.pop() ?? "";
      for (const line of lines) {
        if (line.trim()) yield JSON.parse(line) as ChatEvent;
      }
      if (done) break;
    }
    if (buffer.trim()) throw new Error("串流回應不完整");
  } finally {
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
  for await (const event of parseNdjson(response.body)) {
    if (terminal) throw new Error("串流完成後收到額外事件");
    terminal = event.type === "done" || event.type === "error";
    yield event;
  }
  if (!terminal) throw new Error("串流回應不完整");
}
