import { FormEvent, useCallback, useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  ApiError,
  clearMessages,
  getMessages,
  getSession,
  getSyncJob,
  login,
  logout,
  streamChat,
  uploadManual,
  type SyncJob,
} from "./api";
import type { ChatMessage } from "./types";

type AuthState = "checking" | "authenticated" | "unauthenticated";
type View = "chat" | "documents";

const suggestions = ["如何切換盤查邊界？", "忘記密碼怎麼辦？", "系統有哪些角色？"];
const maxManualBytes = 25 * 1024 * 1024;
const allowedManualExtensions = [".pdf", ".xlsx", ".csv"];

function uid(): string {
  return globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random()}`;
}

function LoginView({ onSuccess }: { onSuccess: () => void }) {
  const [apiKey, setApiKey] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!apiKey || submitting) return;
    setSubmitting(true);
    setError("");
    try {
      await login(apiKey);
      setApiKey("");
      onSuccess();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "登入失敗");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <main className="login-page">
      <form className="login-card" onSubmit={submit}>
        <div className="brand-mark" aria-hidden="true">C</div>
        <h1>碳管理知識助手</h1>
        <p>使用 APP 登入密碼進入知識問答系統。</p>
        <label htmlFor="api-key">APP 登入密碼</label>
        <input
          id="api-key"
          type="password"
          autoComplete="current-password"
          value={apiKey}
          onChange={(event) => setApiKey(event.target.value)}
          required
        />
        {error && <p className="form-error" role="alert">{error}</p>}
        <button className="primary-button" type="submit" disabled={submitting}>
          {submitting ? "登入中…" : "登入"}
        </button>
      </form>
    </main>
  );
}

function MessageBubble({ message }: { message: ChatMessage }) {
  return (
    <article className={`message ${message.role}`}>
      {message.role === "assistant" && <span className="assistant-mark" aria-hidden="true">C</span>}
      <div className="message-bubble">
        {message.role === "assistant" ? (
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{message.content || "…"}</ReactMarkdown>
        ) : (
          <p>{message.content}</p>
        )}
        {message.sources && message.sources.length > 0 && (
          <details className="sources">
            <summary>查看 {message.sources.length} 筆參考資料</summary>
            <ol>{message.sources.map((source) => <li key={source}>{source}</li>)}</ol>
          </details>
        )}
        {message.role === "assistant" && (message.elapsedMs !== undefined || message.incomplete) && (
          <div className="message-meta">
            {message.incomplete ? "已停止接收" : message.responseSource === "cache" ? "快取回答" : "知識助手"}
            {message.elapsedMs !== undefined && <span>{(message.elapsedMs / 1000).toFixed(1)} 秒</span>}
          </div>
        )}
      </div>
    </article>
  );
}

function ChatView({ onUnauthorized }: { onUnauthorized: () => void }) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [draft, setDraft] = useState("");
  const [statusText, setStatusText] = useState("");
  const [error, setError] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [loadingHistory, setLoadingHistory] = useState(true);
  const [clearing, setClearing] = useState(false);
  const abortRef = useRef<AbortController | null>(null);
  const endRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    let active = true;
    getMessages()
      .then((items) => { if (active) setMessages(items); })
      .catch((reason) => {
        if (!active) return;
        if (reason instanceof ApiError && reason.status === 401) onUnauthorized();
        else setError(reason instanceof Error ? reason.message : "無法載入對話");
      })
      .finally(() => { if (active) setLoadingHistory(false); });
    return () => { active = false; abortRef.current?.abort(); };
  }, [onUnauthorized]);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages, statusText]);

  async function submitMessage(text = draft) {
    const message = text.trim();
    if (!message || message.length > 2000 || streaming || loadingHistory || clearing) return;

    const assistantId = uid();
    setMessages((current) => [
      ...current,
      { id: uid(), role: "user", content: message },
      { id: assistantId, role: "assistant", content: "" },
    ]);
    setDraft("");
    setError("");
    setStreaming(true);
    const controller = new AbortController();
    abortRef.current = controller;

    try {
      for await (const event of streamChat(message, controller.signal)) {
        if (event.type === "status") setStatusText(event.message);
        if (event.type === "delta") {
          setStatusText("");
          setMessages((current) => current.map((item) =>
            item.id === assistantId ? { ...item, content: item.content + event.text } : item,
          ));
        }
        if (event.type === "sources") {
          setMessages((current) => current.map((item) =>
            item.id === assistantId ? { ...item, sources: event.items.map(({ label }) => label) } : item,
          ));
        }
        if (event.type === "metadata") {
          setMessages((current) => current.map((item) =>
            item.id === assistantId ? {
              ...item,
              elapsedMs: event.elapsedMs,
              responseSource: event.responseSource,
              cacheHit: event.cacheHit,
            } : item,
          ));
        }
        if (event.type === "error") throw new Error(event.message);
      }
    } catch (reason) {
      if (controller.signal.aborted) {
        setMessages((current) => current.map((item) =>
          item.id === assistantId ? { ...item, incomplete: true } : item,
        ));
      } else if (reason instanceof ApiError && reason.status === 401) {
        onUnauthorized();
      } else {
        setError(reason instanceof Error ? reason.message : "回答失敗，請稍後再試");
        setMessages((current) => current.filter((item) => item.id !== assistantId || item.content));
      }
    } finally {
      abortRef.current = null;
      setStreaming(false);
      setStatusText("");
    }
  }

  async function clearConversation() {
    if (streaming || loadingHistory || clearing) return;
    if (!window.confirm("清除目前對話脈絡？此操作不會刪除系統稽核紀錄。")) return;
    setClearing(true);
    setError("");
    try {
      await clearMessages();
      setMessages([]);
    } catch (reason) {
      if (reason instanceof ApiError && reason.status === 401) onUnauthorized();
      else setError(reason instanceof Error ? reason.message : "無法清除對話");
    } finally {
      setClearing(false);
    }
  }

  return (
    <section className="page chat-page">
      <header className="page-heading">
        <div><h1>知識問答</h1><p>根據操作手冊提供回答</p></div>
        <button className="icon-button" type="button" aria-label="清除對話" onClick={clearConversation} disabled={streaming || loadingHistory || clearing}>⌫</button>
      </header>

      <div className="thread" aria-live="polite">
        {messages.length === 0 && (
          <div className="empty-state">
            <span className="assistant-mark large" aria-hidden="true">C</span>
            <h2>想了解什麼？</h2>
            <p>詢問碳管理系統操作、排放源或報表相關問題。</p>
          </div>
        )}
        {messages.map((message) => <MessageBubble key={message.id} message={message} />)}
        {statusText && <p className="stream-status">{statusText}</p>}
        {error && <p className="inline-error" role="alert">{error}</p>}
        <div ref={endRef} />
      </div>

      <div className="composer-zone">
        <div className="suggestions" aria-label="建議問題">
          {suggestions.map((suggestion) => (
            <button key={suggestion} type="button" onClick={() => submitMessage(suggestion)} disabled={streaming || loadingHistory || clearing}>
              {suggestion}
            </button>
          ))}
        </div>
        <form className="composer" onSubmit={(event) => { event.preventDefault(); submitMessage(); }}>
          <textarea
            aria-label="輸入問題"
            rows={1}
            maxLength={2000}
            placeholder="輸入你的問題…"
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
                event.preventDefault();
                submitMessage();
              }
            }}
            disabled={streaming || loadingHistory || clearing}
          />
          {streaming ? (
            <button className="stop-button" type="button" onClick={() => abortRef.current?.abort()} aria-label="停止接收">■</button>
          ) : (
            <button className="send-button" type="submit" disabled={!draft.trim()} aria-label="送出問題">↑</button>
          )}
        </form>
        <p className="safety-note">請勿輸入密碼、API key 或個人敏感資料</p>
      </div>
    </section>
  );
}

function DocumentsView({ onUnauthorized }: { onUnauthorized: () => void }) {
  const [file, setFile] = useState<File | null>(null);
  const [job, setJob] = useState<SyncJob | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [pollAttempt, setPollAttempt] = useState(0);
  const [pollStopped, setPollStopped] = useState(false);

  useEffect(() => {
    if (!job || !["queued", "running"].includes(job.status)) return;
    let active = true;
    let timer: number;
    const deadline = Date.now() + 10 * 60 * 1000;
    setPollStopped(false);
    async function poll() {
      if (!active) return;
      if (Date.now() >= deadline) {
        setError("同步狀態查詢逾時，可手動重新查詢。");
        setPollStopped(true);
        return;
      }
      try {
        const latest = await getSyncJob(job!.jobId);
        if (!active) return;
        setJob(latest);
        if (!["succeeded", "failed"].includes(latest.status)) {
          timer = window.setTimeout(poll, document.hidden ? 5000 : 2000);
        }
      } catch (reason) {
        if (!active) return;
        if (reason instanceof ApiError && reason.status === 401) onUnauthorized();
        else {
          setError(reason instanceof Error ? reason.message : "無法取得同步狀態");
          setPollStopped(true);
        }
      }
    }
    timer = window.setTimeout(poll, document.hidden ? 5000 : 2000);
    return () => { active = false; window.clearTimeout(timer); };
  }, [job?.jobId, pollAttempt, onUnauthorized]);

  async function upload() {
    if (!file || busy) return;
    setBusy(true);
    setError("");
    try {
      setJob(await uploadManual(file));
    } catch (reason) {
      if (reason instanceof ApiError && reason.status === 401) onUnauthorized();
      else setError(reason instanceof Error ? reason.message : "上傳失敗");
    } finally {
      setBusy(false);
    }
  }

  function selectFile(selected: File | null) {
    setError("");
    setJob(null);
    if (!selected) {
      setFile(null);
      return;
    }
    const lowerName = selected.name.toLowerCase();
    if (!allowedManualExtensions.some((extension) => lowerName.endsWith(extension))) {
      setFile(null);
      setError("只支援 PDF、XLSX、CSV 檔案");
      return;
    }
    if (selected.size > maxManualBytes) {
      setFile(null);
      setError("檔案不可超過 25 MB");
      return;
    }
    setFile(selected);
  }

  return (
    <section className="page documents-page">
      <header className="page-heading"><div><h1>文件管理</h1><p>管理功能 · 所有登入者皆可上傳</p></div></header>
      <div className="upload-card">
        <div className="upload-mark" aria-hidden="true">⇧</div>
        <h2>選擇裝置中的文件</h2>
        <p>支援 PDF、XLSX、CSV，單檔上限 25 MB</p>
        <label className="file-picker">
          <span>{file ? file.name : "選擇檔案"}</span>
          <input
            aria-label="選擇手冊檔案"
            type="file"
            accept=".pdf,.xlsx,.csv"
            onChange={(event) => selectFile(event.target.files?.[0] ?? null)}
          />
        </label>
        <button className="primary-button" type="button" onClick={upload} disabled={!file || busy}>
          {busy ? "上傳中…" : "上傳並更新知識庫"}
        </button>
      </div>
      {job && (
        <div className="job-card" aria-live="polite">
          <div><strong>{file?.name}</strong><span>{job.status}</span></div>
          <progress max="100" value={job.status === "queued" ? 20 : job.status === "running" ? 65 : 100} />
          <p>{job.message ?? "離開此頁不會中斷背景作業。"}</p>
        </div>
      )}
      {error && <p className="inline-error" role="alert">{error}</p>}
      {pollStopped && <button type="button" className="primary-button" onClick={() => { setError(""); setPollAttempt((value) => value + 1); }}>重新查詢同步狀態</button>}
    </section>
  );
}

function Shell({ onLogout, onUnauthorized, logoutError }: { onLogout: () => void; onUnauthorized: () => void; logoutError: string }) {
  const fromPath = () => window.location.pathname === "/admin" ? "documents" : "chat";
  const [view, setView] = useState<View>(fromPath);
  useEffect(() => {
    const update = () => setView(fromPath());
    window.addEventListener("popstate", update);
    return () => window.removeEventListener("popstate", update);
  }, []);
  function navigate(next: View) {
    window.history.pushState(null, "", next === "documents" ? "/admin" : "/");
    setView(next);
  }
  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="brand-mark small" aria-hidden="true">C</div>
        <div className="brand-title"><strong>碳管理知識助手</strong><span>安全連線 · 已登入</span></div>
        <button className="icon-button" type="button" onClick={onLogout} aria-label="登出">↪</button>
      </header>
      {logoutError && <p className="inline-error" role="alert">{logoutError}</p>}
      <main>{view === "chat" ? <ChatView onUnauthorized={onUnauthorized} /> : <DocumentsView onUnauthorized={onUnauthorized} />}</main>
      <nav className="bottom-nav" aria-label="主要功能">
        <button type="button" aria-current={view === "chat" ? "page" : undefined} onClick={() => navigate("chat")}>
          <span aria-hidden="true">□</span>知識問答
        </button>
        <button type="button" aria-current={view === "documents" ? "page" : undefined} onClick={() => navigate("documents")}>
          <span aria-hidden="true">▱</span>文件管理
        </button>
      </nav>
    </div>
  );
}

export default function App() {
  const [auth, setAuth] = useState<AuthState>("checking");
  const [logoutError, setLogoutError] = useState("");
  const unauthorized = useCallback(() => setAuth("unauthenticated"), []);

  useEffect(() => {
    getSession()
      .then((session) => setAuth(session.authenticated ? "authenticated" : "unauthenticated"))
      .catch((reason) => setAuth(reason instanceof ApiError && reason.status === 401 ? "unauthenticated" : "unauthenticated"));
  }, []);

  if (auth === "checking") return <main className="loading-page" aria-live="polite">載入中…</main>;
  if (auth === "unauthenticated") return <LoginView onSuccess={() => setAuth("authenticated")} />;

  return (
    <>
    <Shell
      logoutError={logoutError}
      onUnauthorized={unauthorized}
      onLogout={async () => {
        setLogoutError("");
        try { await logout(); setAuth("unauthenticated"); }
        catch (reason) { setLogoutError(reason instanceof Error ? reason.message : "登出失敗，請重試"); }
      }}
    />
    </>
  );
}
