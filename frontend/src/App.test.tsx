import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import * as api from "./api";

vi.mock("./api", async (original) => ({
  ...await original<typeof import("./api")>(),
  getSession: vi.fn(), getMessages: vi.fn(), clearMessages: vi.fn(),
  login: vi.fn(), logout: vi.fn(), streamChat: vi.fn(),
  uploadManual: vi.fn(), getSyncJob: vi.fn(),
}));

beforeEach(() => {
  vi.resetAllMocks();
  window.history.replaceState(null, "", "/");
  vi.mocked(api.getSession).mockResolvedValue({ authenticated: true, authEnabled: true });
  vi.mocked(api.getMessages).mockResolvedValue([]);
  vi.mocked(api.clearMessages).mockResolvedValue();
  vi.mocked(api.logout).mockResolvedValue();
  Element.prototype.scrollTo = vi.fn();
});
afterEach(() => { cleanup(); vi.useRealTimers(); });

async function openChat() {
  render(<App />);
  await waitFor(() => expect(screen.getByRole("textbox", { name: "輸入問題" })).toBeEnabled(), { timeout: 5000 });
}
async function send(text = "問題") {
  fireEvent.change(screen.getByRole("textbox", { name: "輸入問題" }), { target: { value: text } });
  fireEvent.click(screen.getByRole("button", { name: "送出問題" }));
}
function answer(content = "完成回答", sources = true) {
  vi.mocked(api.streamChat).mockImplementation(async function* () {
    yield { type: "status", stage: "retrieving", message: "檢索中" };
    yield { type: "delta", text: content };
    if (sources) yield { type: "sources", items: [{ label: "操作手冊" }] };
    yield { type: "metadata", elapsedMs: 1200, responseSource: "rag", cacheHit: false };
    yield { type: "done" };
  });
}

describe("chat acceptance", () => {
  it("restores history and only clears after server success", async () => {
    vi.mocked(api.getMessages).mockResolvedValue([{ id: "h1", role: "assistant", content: "既有回答" }]);
    vi.spyOn(window, "confirm").mockReturnValue(true);
    vi.mocked(api.clearMessages).mockRejectedValueOnce(new api.ApiError("清除失敗", 503));
    await openChat();
    fireEvent.click(screen.getByRole("button", { name: "清除對話" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("清除失敗");
    expect(screen.getByText("既有回答")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "清除對話" }));
    await waitFor(() => expect(screen.queryByText("既有回答")).not.toBeInTheDocument());
  });
  it("prevents submitting before history restoration finishes", async () => {
    let finish!: (value: []) => void;
    vi.mocked(api.getMessages).mockReturnValue(new Promise((resolve) => { finish = resolve; }));
    render(<App />);
    expect(await screen.findByRole("textbox", { name: "輸入問題" })).toBeDisabled();
    await act(async () => finish([]));
    expect(screen.getByRole("textbox", { name: "輸入問題" })).toBeEnabled();
  });
  it("renders answer, sources and timing", async () => {
    answer(); await openChat(); await send();
    expect(await screen.findByText("完成回答")).toBeInTheDocument();
    expect(await screen.findByText("查看 1 筆參考資料")).toBeInTheDocument();
    expect(await screen.findByText("1.2 秒")).toBeInTheDocument();
  });
  it("disables clear during streaming and supports stop", async () => {
    vi.mocked(api.streamChat).mockImplementation(async function* (_message, signal) {
      yield { type: "delta", text: "部分回答" };
      await new Promise((_resolve, reject) => signal.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError"))));
    });
    await openChat(); await send();
    await screen.findByText("部分回答");
    expect(screen.getByRole("button", { name: "清除對話" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "停止接收" }));
    expect(await screen.findByText("已停止接收")).toBeInTheDocument();
  });
  it("does not execute raw HTML or unsafe Markdown URLs", async () => {
    answer('<img src=x onerror="window.hacked=true">\n\n[危險](javascript:alert(1))', false);
    await openChat(); await send();
    await screen.findByText("危險");
    expect(document.querySelector(".message-bubble img")).toBeNull();
    expect(document.querySelector('a[href^="javascript:"]')).toBeNull();
    expect(screen.queryByText(/筆參考資料/)).toBeNull();
  });
  it("does not submit during IME composition", async () => {
    await openChat();
    const input = screen.getByRole("textbox", { name: "輸入問題" });
    fireEvent.change(input, { target: { value: "中文" } });
    fireEvent.keyDown(input, { key: "Enter", isComposing: true });
    expect(api.streamChat).not.toHaveBeenCalled();
  });
  it("keeps the composer focused when tapping send before submitting", async () => {
    answer(); await openChat();
    const input = screen.getByRole("textbox", { name: "輸入問題" });
    fireEvent.change(input, { target: { value: "手機送出" } });
    input.focus();
    const button = screen.getByRole("button", { name: "送出問題" });
    const pointer = new Event("pointerdown", { bubbles: true, cancelable: true });
    // Browser default focus transfer dismisses iOS's keyboard before click.
    if (button.dispatchEvent(pointer)) button.focus();
    expect(input).toHaveFocus();
    fireEvent.click(button);
    expect(await screen.findByText("完成回答")).toBeInTheDocument();
    expect(api.streamChat).toHaveBeenCalledWith("手機送出", expect.any(AbortSignal));
  });
  it("returns to login on chat 401", async () => {
    vi.mocked(api.streamChat).mockImplementation(async function* () { yield* []; throw new api.ApiError("已過期", 401); });
    await openChat(); await send();
    expect(await screen.findByLabelText("APP 登入密碼")).toBeInTheDocument();
  });
  it("keeps the app visible and reports failed logout", async () => {
    vi.mocked(api.logout).mockRejectedValue(new api.ApiError("登出失敗", 503));
    await openChat(); fireEvent.click(screen.getByRole("button", { name: "登出" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("登出失敗");
    expect(screen.getByRole("textbox", { name: "輸入問題" })).toBeInTheDocument();
  });
});

describe("manual upload acceptance", () => {
  async function openDocuments() {
    await openChat(); fireEvent.click(screen.getByRole("button", { name: "文件管理" }));
  }
  it("rejects unsupported and oversized files before sending", async () => {
    await openDocuments();
    const input = screen.getByLabelText("選擇手冊檔案");
    fireEvent.change(input, { target: { files: [new File(["x"], "manual.exe")] } });
    expect(screen.getByRole("alert")).toHaveTextContent("只支援");
    const large = new File(["x"], "manual.csv", { type: "text/csv" });
    Object.defineProperty(large, "size", { value: 26 * 1024 * 1024 });
    fireEvent.change(input, { target: { files: [large] } });
    expect(screen.getByRole("alert")).toHaveTextContent("25 MB");
    expect(api.uploadManual).not.toHaveBeenCalled();
  });
  it("returns to login on upload 401", async () => {
    vi.mocked(api.uploadManual).mockRejectedValue(new api.ApiError("已過期", 401));
    await openDocuments();
    fireEvent.change(screen.getByLabelText("選擇手冊檔案"), { target: { files: [new File(["x"], "manual.csv", { type: "text/csv" })] } });
    fireEvent.click(screen.getByRole("button", { name: "上傳並更新知識庫" }));
    expect(await screen.findByLabelText("APP 登入密碼")).toBeInTheDocument();
  });
  it("polls until success then stops", async () => {
    vi.mocked(api.uploadManual).mockResolvedValue({ jobId: "job-1", status: "queued" });
    vi.mocked(api.getSyncJob).mockResolvedValue({ jobId: "job-1", status: "succeeded" });
    await openDocuments();
    vi.useFakeTimers();
    fireEvent.change(screen.getByLabelText("選擇手冊檔案"), { target: { files: [new File(["x"], "manual.csv")] } });
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "上傳並更新知識庫" })));
    await act(async () => vi.advanceTimersByTimeAsync(2000));
    expect(screen.getByText("succeeded")).toBeInTheDocument();
    await act(async () => vi.advanceTimersByTimeAsync(10000));
    expect(api.getSyncJob).toHaveBeenCalledTimes(1);
  });
  it("offers manual retry after polling failure", async () => {
    vi.mocked(api.uploadManual).mockResolvedValue({ jobId: "job-1", status: "queued" });
    vi.mocked(api.getSyncJob).mockRejectedValueOnce(new api.ApiError("暫時無法取得同步狀態", 503)).mockResolvedValue({ jobId: "job-1", status: "succeeded" });
    await openDocuments(); vi.useFakeTimers();
    fireEvent.change(screen.getByLabelText("選擇手冊檔案"), { target: { files: [new File(["x"], "manual.csv")] } });
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "上傳並更新知識庫" })));
    await act(async () => vi.advanceTimersByTimeAsync(2000));
    fireEvent.click(screen.getByRole("button", { name: "重新查詢同步狀態" }));
    await act(async () => vi.advanceTimersByTimeAsync(2000));
    expect(screen.getByText("succeeded")).toBeInTheDocument();
  });
});


it("restores the admin route on refresh", async () => {
  window.history.replaceState(null, "", "/admin");
  render(<App />);
  expect(await screen.findByRole("heading", { name: "文件管理" })).toBeInTheDocument();
});


describe("remaining acceptance scenarios", () => {
  async function startJob(result: api.SyncJob) {
    vi.mocked(api.uploadManual).mockResolvedValue(result);
    await openChat();
    fireEvent.click(screen.getByRole("button", { name: "文件管理" }));
    vi.useFakeTimers();
    fireEvent.drop(document.querySelector(".upload-card")!, { dataTransfer: { files: [new File(["q,a"], "drop.csv", { type: "text/csv" })] } });
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "上傳並更新知識庫" })));
  }
  it("validates a dropped file and exposes indeterminate upload progress", async () => {
    let finish!: (value: api.SyncJob) => void;
    vi.mocked(api.uploadManual).mockReturnValue(new Promise((resolve) => { finish = resolve; }));
    await openChat(); fireEvent.click(screen.getByRole("button", { name: "文件管理" }));
    const card = document.querySelector(".upload-card")!;
    fireEvent.drop(card, { dataTransfer: { files: [new File(["x"], "bad.exe")] } });
    expect(screen.getByRole("alert")).toHaveTextContent("只支援");
    fireEvent.drop(card, { dataTransfer: { files: [new File(["q,a"], "drop.csv", { type: "text/csv" })] } });
    fireEvent.click(screen.getByRole("button", { name: "上傳並更新知識庫" }));
    expect(screen.getByRole("progressbar", { name: "上傳中" })).not.toHaveAttribute("value");
    expect(screen.getByLabelText("選擇手冊檔案")).toBeDisabled();
    await act(async () => finish({ jobId: "done-job", status: "succeeded" }));
    expect(screen.getByText("succeeded")).toBeInTheDocument();
  });
  it.each([404, 503])("stops on job HTTP %s and offers retry", async (status) => {
    vi.mocked(api.getSyncJob).mockRejectedValue(new api.ApiError("查詢失敗", status));
    await startJob({ jobId: "job", status: "queued" });
    await act(async () => vi.advanceTimersByTimeAsync(2000));
    expect(screen.getByRole("alert")).toHaveTextContent("查詢失敗");
    expect(screen.getByRole("button", { name: "重新查詢同步狀態" })).toBeInTheDocument();
    await act(async () => vi.advanceTimersByTimeAsync(10000));
    expect(api.getSyncJob).toHaveBeenCalledTimes(1);
  });
  it("returns to login on polling 401", async () => {
    vi.mocked(api.getSyncJob).mockRejectedValue(new api.ApiError("已過期", 401));
    await startJob({ jobId: "job", status: "queued" });
    await act(async () => vi.advanceTimersByTimeAsync(2000));
    expect(screen.getByLabelText("APP 登入密碼")).toBeInTheDocument();
  });
  it("stops on terminal job failure", async () => {
    vi.mocked(api.getSyncJob).mockResolvedValue({ jobId: "job", status: "failed", message: "同步失敗" });
    await startJob({ jobId: "job", status: "queued" });
    await act(async () => vi.advanceTimersByTimeAsync(2000));
    expect(screen.getByText("failed")).toBeInTheDocument();
    await act(async () => vi.advanceTimersByTimeAsync(10000));
    expect(api.getSyncJob).toHaveBeenCalledTimes(1);
  });
  it("times out after ten minutes and supports manual restart", async () => {
    vi.mocked(api.getSyncJob).mockResolvedValue({ jobId: "job", status: "running" });
    await startJob({ jobId: "job", status: "queued" });
    await act(async () => vi.advanceTimersByTimeAsync(602000));
    expect(screen.getByRole("alert")).toHaveTextContent("逾時");
    const calls = vi.mocked(api.getSyncJob).mock.calls.length;
    await act(async () => vi.advanceTimersByTimeAsync(10000));
    expect(api.getSyncJob).toHaveBeenCalledTimes(calls);
    vi.mocked(api.getSyncJob).mockResolvedValue({ jobId: "job", status: "succeeded" });
    fireEvent.click(screen.getByRole("button", { name: "重新查詢同步狀態" }));
    await act(async () => vi.advanceTimersByTimeAsync(2000));
    expect(screen.getByText("succeeded")).toBeInTheDocument();
  });
  it("slows polling while the page is hidden", async () => {
    const spy = vi.spyOn(document, "hidden", "get").mockReturnValue(true);
    vi.mocked(api.getSyncJob).mockResolvedValue({ jobId: "job", status: "succeeded" });
    await startJob({ jobId: "job", status: "queued" });
    await act(async () => vi.advanceTimersByTimeAsync(2000));
    expect(api.getSyncJob).not.toHaveBeenCalled();
    await act(async () => vi.advanceTimersByTimeAsync(3000));
    expect(api.getSyncJob).toHaveBeenCalledTimes(1);
    spy.mockRestore();
  });
  it("observes Retry-After without automatically resubmitting", async () => {
    vi.mocked(api.streamChat).mockImplementation(async function* () { yield* []; throw new api.ApiError("請於 12 秒後重試", 429, 12); });
    await openChat(); vi.useFakeTimers();
    await act(async () => send());
    fireEvent.change(screen.getByRole("textbox", { name: "輸入問題" }), { target: { value: "重試" } });
    expect(screen.getByRole("button", { name: "送出問題" })).toBeDisabled();
    await act(async () => vi.advanceTimersByTimeAsync(12000));
    expect(screen.getByRole("button", { name: "送出問題" })).toBeEnabled();
    expect(api.streamChat).toHaveBeenCalledTimes(1);
  });
});
