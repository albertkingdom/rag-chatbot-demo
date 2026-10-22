<!-- SPECTRA:START v1.0.2 -->

# Spectra Instructions

This project uses Spectra for Spec-Driven Development(SDD). Specs live in `openspec/specs/`, change proposals in `openspec/changes/`.

## Use `/spectra-*` skills when:

- A discussion needs structure before coding → `/spectra-discuss`
- User wants to plan, propose, or design a change → `/spectra-propose`
- Tasks are ready to implement → `/spectra-apply`
- There's an in-progress change to continue → `/spectra-ingest`
- User asks about specs or how something works → `/spectra-ask`
- Implementation is done → `/spectra-archive`
- Commit only files related to a specific change → `/spectra-commit`

## Workflow

discuss? → propose → apply ⇄ ingest → archive

- `discuss` is optional — skip if requirements are clear
- Requirements change mid-work? Plan mode → `ingest` → resume `apply`

## Parked Changes

Changes can be parked（暫存）— temporarily moved out of `openspec/changes/`. Parked changes won't appear in `spectra list` but can be found with `spectra list --parked`. To restore: `spectra unpark <name>`. The `/spectra-apply` and `/spectra-ingest` skills handle parked changes automatically.

<!-- SPECTRA:END -->

## 開發規則

### 分支

- 新功能一律從 `master`、`main` 或 `develop` 開新分支開發。
- 分支命名：`feature/{feature-name}`。
- 實作前必須有規劃文件或已完成的 Spectra 流程（`/spectra-propose`）。
- 合併前更新 `README.md`，反映新功能。
- 開 PR 前必須完成 code review，且所有阻擋與重要問題都已解決。
- review 發現的問題依 `review-findings` skill（`.claude/skills/review-findings/`）處理。

### Commit 訊息

- Commit 訊息不得提及任何 AI 工具（例如 Claude、Copilot、ChatGPT 或其他 AI 助手）。
- commit 與 PR 說明用英文，採 Conventional Commits。

### 文件

- `architecture.md` 是 RAG 請求流程、知識庫同步流程、參數與已知問題的正式說明。參數值本身在程式碼裡（`src/config.py` 及其 Key Parameters 表列出的檔案）；行為需求在 `openspec/specs/`。
- 修改下列檔案時，要在同一個變更裡更新 `architecture.md`：`src/chat_service.py`、`src/query_router.py`、`src/hybrid_retriever.py`、`src/bm25_index.py`、`src/rerank_stage.py`、`src/retrieval_grader.py`、`src/rag_pipeline.py`、`src/cache_service.py`、`src/guardrails.py`、`src/build_vector_store.py`、`src/manual_service.py`、`src/services.py` 的模型設定，或 `src/config.py` 的值。
- `README.md` 只當入口（概覽、安裝、API、部署、限制）。新功能只在 README 功能列表加一條，細節寫在 `architecture.md`，不重複。
- 圖表用 Markdown 內的 Mermaid 繪製。已淘汰的圖片圖表放在 `docs/archive/`。

## 專案地圖

| 路徑 | 角色 |
|---|---|
| `src/chat_service.py` | 請求流程：輸入防護 → 路由 →（rag：快取 → 檢索 → 重排 → 評分／重試）→ 生成 → 輸出防護 → 儲存；以 NDJSON 事件串流回應 |
| `src/query_router.py`、`hybrid_retriever.py`、`bm25_index.py`、`rerank_stage.py`、`retrieval_grader.py` | 路由與檢索各階段 |
| `src/cache_service.py`、`chat_history_service.py`、`conversation_db.py` | Redis 語意快取、短期對話歷史、MongoDB 對話紀錄 |
| `src/guardrails.py`、`access_control.py` | 輸入／輸出防護；API key 與 session 驗證 |
| `src/build_vector_store.py`、`manual_service.py` | 知識庫同步：上傳、增量同步 Pinecone 與 BM25 |
| `src/config.py` | 可調參數（見 `architecture.md` 的 Key Parameters） |
| `src/api/routes.py`、`src/chat_events.py` | HTTP 路由與有型別的 NDJSON 事件模型 |
| `frontend/` | React + TypeScript + Vite 介面（pnpm） |
| `tests/` | pytest 測試（`test_*.py`），以及會呼叫真實服務的評估腳本 |
| `scripts/` | 評估、smoke test、依賴鎖定 |
| `openspec/specs/`、`openspec/changes/` | 行為規格與變更提案（Spectra） |
| `terraform/` | Cloud Run 部署 |

## 指令

```bash
python -m pytest tests -q                                   # 後端測試（不需要 API key）
cd frontend && pnpm lint && pnpm build && pnpm exec vitest run --maxWorkers=1
cd terraform && terraform fmt -check -recursive && terraform init -backend=false -input=false && terraform validate
scripts/compile_requirements.sh                             # 改 requirements.in 之後執行；兩個檔案一起 commit
docker compose up --build                                   # 本機啟動（.env 需要真實 API key）
```

CI（`.github/workflows/ci.yml`）每個 PR 都會跑前三項，不跑 RAGAS，也不呼叫真實 LLM。評估（`tests/evaluate_rag.py`、`scripts/eval_sample_manual.py`）只在本機執行，而且**不得對正式 index 執行**。

## 規格

- 新增或改變行為的變更，實作前要有 Spectra change（`/spectra-propose`）。規格描述可觀察的行為，不描述程式結構。
- `openspec/specs/` 下一個 capability 一個資料夾，依行為用 kebab-case 命名（`semantic-cache-lookup`、`low-confidence-handling`）。
- 需求用 `SHALL`／`SHALL NOT`；每條需求至少一個 `#### Scenario:`，以 WHEN／THEN 撰寫。
- 參數值留在程式碼（`src/config.py`）；規格寫規則（例如「嚴格大於門檻」），不寫數字。
- 已歸檔的 change 是歷史紀錄，不要修改；需要時另開新的 change。
- 只動文件或工具設定的變更（README、`.claude/`、CI 設定）可以不走 Spectra 流程，並在 PR 說明寫明。

## Skills 與 Hooks

專案專屬的 skill 放在 `.agents/skills/`（不含 Claude Code 專屬機制，其他支援該目錄的工具也能讀）和 `.claude/skills/`（用到 Claude Code 專屬機制的）；`.claude/skills/` 裡有 symlink 指向 `.agents/skills/`，Claude Code 靠它載入。`spectra-*`、`langchain-*`、`langgraph-*`、`deep-agents-*` 是通用 skill，`spectra-wrapup` 負責收尾（歸檔、分批 commit、review）。

| Skill | 使用時機 |
|---|---|
| `project-review` | 開 PR 前找問題；套用本專案的檢查清單並記錄結果（取名避開內建的 `/code-review`） |
| `review-findings` | 處理 review 發現的問題：驗證、分級、用新 commit 修正、重審 |
| `unit-test` | 撰寫或修改測試 |
| `create-pr` | push 並開 PR |
| `eval-rag` | 改了檢索、路由、提示詞或模型後，在 demo index 跑 RAGAS 並與上一份報告比較 |

Hooks（`.claude/settings.json`，腳本在 `.claude/hooks/`）把上面的規則變成閘門。它們讀指令文字做判斷，是防止疏忽的護欄，不是安全邊界；需要 `jq`（沒有時 `guard-bash.sh` 會顯示警告並停用，不會擋住所有指令）。改 hook 後執行 `.claude/hooks/test-hooks.sh` 做回歸測試。

| Hook | 作用 |
|---|---|
| `guard-bash.sh`（PreToolUse，Bash） | 擋下 `git add -A`／`.`／`-u` 與 `git commit -a`、在 `master`／`main`／`develop` 上 commit、帶 AI 署名行（`Co-Authored-By`、`Generated with`）的 commit 訊息，以及 HEAD 沒有審查紀錄時的 `gh pr create`（只改文件與規格的分支免審；歸檔產生的 `openspec/` commit 不會讓審查紀錄失效）。`git commit` 與 `gh pr create` 不能串在同一個指令 |
| `remind-docs.sh`（PostToolUse，Edit/Write） | 編輯 `architecture.md` 涵蓋的檔案時，每個檔案每個 HEAD 提醒一次同步更新它 |
| `review-reminder.sh`（Stop） | 分支上有未審查的程式碼 commit 時，每個 HEAD 提醒一次要做 code review（程式碼範圍同 `guard-bash.sh`） |
| `mark-reviewed.sh` | 把 HEAD 的審查結果記在 `.claude/state/`（不進版控），可在任何子目錄執行；由 skill 呼叫，不要手動執行 |
