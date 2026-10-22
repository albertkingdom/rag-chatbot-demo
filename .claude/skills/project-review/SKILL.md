---
name: project-review
description: "依這個專案的 RAG 管線與規則審查變更、找出問題：跑自動檢查、依變更區域套用檢查清單（檢索與路由、語意快取、guardrail、NDJSON 串流、存取控制、知識庫同步、前端、Terraform、文件同步）、問題分級（阻擋／重要／建議），通過後記錄審查結果讓 PR 閘門放行。當使用者說「code review」「審查」「review 一下」「準備開 PR」「可以開 PR 嗎」，或功能做完要開 PR 前使用。找到問題之後怎麼處理交給 review-findings。"
---

# Code review（RAG demo 專案）

專案規則（CLAUDE.md）：**review 完成、阻擋與重要問題都處理完，才能開 PR。** `.claude/hooks/guard-bash.sh` 會在 `gh pr create` 前檢查審查紀錄是否對應目前的 HEAD，沒有就擋下（只改文件與規格的分支除外）。

這個 skill 負責**找問題**；整理、驗證、修正由 `review-findings` 接手。內建的 `/code-review` 可以當第二意見，但它不認識這個專案的規則，不能取代這份清單；這個 skill 取名 `project-review`，以免與它同名。派 subagent 審查時，要把這份清單與步驟交給它，結果仍由你驗證後才記錄。

## 邊界

- 只讀、只跑檢查，**不改程式**、不 push、不開 PR。
- 審查意見不是事實：每一筆都要有證據（讀到的程式、重現步驟或執行輸出），推測的標明「推測」。
- 沒跑過的檢查就寫沒跑，不寫「應該會過」。

## 步驟

### 1. 決定範圍

```bash
git fetch -q origin master
git diff --stat origin/master...HEAD
git log --oneline origin/master..HEAD
```

有未 commit 的變更先提醒使用者；審查對象是已 commit 的範圍。依 `git diff --name-only` 判斷涉及哪些區域（見步驟 3）。

### 2. 自動檢查（依涉及的區域執行）

| 涉及 | 指令 | 備註 |
|---|---|---|
| `src/`、`tests/`、`requirements*` | `python -m pytest tests -q` | 需要 `OPENROUTER_API_KEY` 的測試會 skip，這是預期行為 |
| `frontend/` | `cd frontend && pnpm lint && pnpm build && pnpm exec vitest run --maxWorkers=1` | `build` 已含 typecheck；lint 是 `--max-warnings=0` |
| `terraform/` | `cd terraform && terraform fmt -check -recursive && terraform init -backend=false -input=false && terraform validate` | 不需要憑證 |

CI 不跑 RAGAS，也不呼叫真實 LLM（見 `docs/development.md`）。檢索、路由、提示詞或模型參數有變動時，本機評估是否做過要記入報告；沒做就列為「重要」並說明要補哪個指令（`tests/evaluate_rag.py`，或對範例手冊用 `scripts/eval_sample_manual.py`）。**評估一律不得對正式 index 執行。**

### 3. 依區域套用檢查清單

**檢索與路由**（`query_router.py`、`hybrid_retriever.py`、`bm25_index.py`、`rerank_stage.py`、`retrieval_grader.py`、`config.py`）
- 參數（`VECTOR_TOP_N`、`BM25_TOP_N`、`RRF_K`、`FUSION_TOP_M`、`RERANK_TOP_K`、`GRADE_SCORE_THRESHOLD`…）有改，`architecture.md` 的 Key Parameters 是否同步、有沒有評估證據。
- 環境變數覆寫：無效值要記警告並回到預設，不能讓服務起不來或靜默吞掉。
- 低信心路徑三個行為要一起成立：答案開頭固定加上 `LOW_CONFIDENCE_PREFIX`（`rag_pipeline.py`，由 `chat_service.py` 加在模型回答前面）、不顯示來源、**不寫入語意快取**。只改其中一個就是缺陷。
- 規則層路由的判斷要有對應的 `test_query_router.py` 案例，不能只靠 LLM 路由兜底。

**語意快取**（`cache_service.py`）
- 相似度是「嚴格大於」`CACHE_SIMILARITY_THRESHOLD`，不是大於等於。
- 索引上限 `CACHE_MAX_ENTRIES` 同時是比對範圍；無效值回到 50。
- 快取 key 要穩定（規格 `cache-key-stability`），不同 index 之間要隔離（`cache-namespace`）：demo 與正式環境不能共用快取內容。
- Redis 型別錯誤（WRONGTYPE）、連線失敗時，要退化成不使用快取，不能讓請求失敗。

**Guardrail**（`guardrails.py`）
- 新增或放寬規則時，**同時**要有「該擋」與「不該擋」的測試。這個模組曾因規則過寬擋掉一般問題（v1.1.2 的誤判修正），誤判比漏判更容易被使用者察覺。
- 輸出端的 PII 與注入內容掃描不能因為輸入端通過就略過。

**NDJSON 串流**（`chat_events.py`、`api/routes.py`、`frontend/src/types.ts`、`api.ts`）
- 事件型別只有 `status`、`delta`、`sources`、`metadata`、`done`、`error`。新增或改欄位時，後端 model、`test_api_contract.py`、前端 `types.ts` 與 parser 測試要一起改。
- 錯誤要以 `error` 事件結束，不能讓串流中斷而前端沒有收尾。

**存取控制**（`access_control.py`）
- API key 用常數時間比較；cookie 是 HttpOnly 的不透明 session，原始 key 不得出現在 cookie、URL、Web Storage 或 log。
- 沒有憑證的 API 請求回 401 JSON，不是 redirect；`/health` 不驗證也不限流。

**知識庫同步**（`build_vector_store.py`、`manual_service.py`）
- 文件 ID 要穩定（`document-identity`），重複同步不能產生重複向量。
- 目前「Pinecone 有、本地沒有就刪除」的策略有已知 TODO；動到這段要在 `architecture.md` 的已知問題更新，並確認不會誤刪正式 index 的資料。
- 背景工作的結果要寫入狀態（`sync-job-outcome`），失敗要看得到。

**前端**（`frontend/src/`）
- 不把 API key 放進 localStorage／sessionStorage；錯誤與載入狀態都要有畫面。
- 新元件要有 vitest 測試，不依賴真實 API。

**Terraform 與部署**（`terraform/`、`Dockerfile`、`docker-compose*.yml`、`.github/workflows/`）
- 新增環境變數要同時出現在 `docker-compose.yml` 與 `terraform/`；只改一邊，正式環境就無法調整（`CACHE_MAX_ENTRIES` 目前就是這個已知狀況）。
- 機密不進 `.tf`、`.tfvars`、workflow；`terraform.tfvars` 已被忽略。
- 依賴變更：改 `requirements.in` → `scripts/compile_requirements.sh` → 兩個檔案一起 commit，不手改 `requirements.txt`；Python 版本要與 Dockerfile 一致。

**文件與規格**
- 改了 `architecture.md` 列出的檔案，該檔要同步（CLAUDE.md 的「文件」段列出完整清單）。
- 新功能：README 功能條目加一行，細節放 `architecture.md`，不重複。
- 行為變更若涉及 `openspec/specs/`：要有對應的 change，**不手改已 archive 的產物**。

**每次都看**
- 機密或個人資料（`.env`、金鑰、內部 URL）進了 diff 嗎。
- commit 訊息有沒有帶 AI 署名行（hook 會擋，但歷史上的 commit 要自己看）。
- 新行為有測試嗎，測試是先失敗過才通過的嗎。

### 4. 分級與報告

分級標準與 `review-findings` 相同（阻擋／重要／建議，有爭議從嚴）。每一筆：

```
[阻擋|重要|建議] <file>:<line> <問題一句話>
證據：<讀到的程式／重現／輸出>
建議：<怎麼修>
狀態：已驗證｜推測
```

最後列出：範圍（`origin/master..HEAD` 幾個 commit）、哪些檢查實際執行過與結果、哪些沒跑與原因。

### 5. 通過時記錄

**沒有未處理的阻擋與重要問題，而且步驟 2 的檢查都通過**，才執行：

```bash
"$(git rev-parse --show-toplevel)/.claude/hooks/mark-reviewed.sh"
```

它把目前 HEAD 寫進本機的 `.claude/state/review-passed`（不進版控）。有未 commit 的追蹤檔案時會拒絕，先 commit。之後任何新 commit 都會讓紀錄失效（只動 `openspec/` 的歸檔 commit 除外），修正完要重審並重新記錄。只改文件與規格、不動程式碼的分支，hook 不要求審查紀錄（程式碼的範圍定義在 `.claude/hooks/lib.sh` 的 `CODE_RE`）。

有阻擋或重要問題時**不要記錄**，改交給 `review-findings` 處理。
