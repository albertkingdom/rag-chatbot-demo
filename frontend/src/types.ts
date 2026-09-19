export type ResponseSource = "rag" | "direct" | "cache" | "guardrail";

export type ChatEvent =
  | { type: "status"; stage: "retrieving" | "reranking" | "generating"; message: string }
  | { type: "delta"; text: string }
  | { type: "sources"; items: Array<{ label: string }> }
  | { type: "metadata"; elapsedMs: number; responseSource: ResponseSource; cacheHit: boolean }
  | { type: "done" }
  | { type: "error"; code: "internal_error"; message: string };

export type ChatMessage = {
  id: string;
  role: "user" | "assistant";
  content: string;
  sources?: string[];
  elapsedMs?: number;
  responseSource?: ResponseSource;
  cacheHit?: boolean;
  incomplete?: boolean;
};
