# 業界標準差距分析

> 對照業界標準,本專案目前的差距清單。建立日期:2026-07-01。
> Item #1 已完成(commit `a08d9c0`,branch `feature/auth-and-rate-limit`)。

## 嚴重(安全 / 正確性,應優先處理)

### 1. 應用程式完全無認證,且 Cloud Run 對全網公開
- ~~`terraform/main.tf` 的 `google_cloud_run_v2_service_iam_member.public` 將 Service 開放給 `allUsers`,`src/app.py` 無任何 auth/rate-limit。任何能連網的人都能呼叫 LLM/Pinecone/OpenAI,直接產生費用與濫用風險。~~
- 業界標準:至少加 API key / IAP / Cloud Run IAM 限縮 + rate limit。
- **狀態:已完成(commit `a08d9c0`)** — Spectra change `add-access-control` 已 archived:Cloud Run IAM 從 `allUsers` 改為 `allowed_invoker_members` 限縮;app 層 `src/access_control.py` 加入 API key header + Redis-backed session token(`/login`/`/logout`)+ per-key fixed-window 限流 + 豁免認證的 `/health`;Terraform `APP_API_KEY` 注入 Secret Manager;34 tests hermetic 全綠。

### 2. 檔案上傳有路徑穿越風險
- `src/app.py` 的 `upload_manual_func` 與 `bom_mapper_func` 直接用 `file.name`;無檔案大小限制、無 content-type 驗證、無掃毒。
- 業界標準:白名單副檔名 + size cap + 安全檔名生成。

### 3. Cache key 用 Python 內建 `hash()`,跨行程不穩定
- `src/cache_service.py:147` `abs(hash(tuple(question_embedding[:10])))`。Python 預設啟用 hash randomization(`PYTHONHASHSEED`),同一 embedding 在不同 worker 行程會算出不同 key,導致 cache 永遠 miss、且 index 與 entry 對不上。另外只取前 10 維易碰撞。
- 業界標準:改用 `hashlib.sha256`。

### 4. Guardrails 是脆弱的正則,易被繞過
- `src/guardrails.py` 用關鍵字 regex。同義改寫、Base64、拆字、英文混中文皆可繞過;且把 `password`/`token`/`secret` 列為 injection 會誤殺合法的「忘記密碼」問題(正好是 README 的範例問題)。
- 業界標準:用專用偵測器(Lakera Guard / 自訂分類器)+ 評測對抗樣本。

### 5. 測試本身有 bug,保護網失靈
- `tests/test_rag_stream.py:14` `from app import rag_chain` — 但模組是 `src.app` 且 `rag_chain` 已不存在(重構後改成 `get_retrieval_chain()`)。此測試匯入即崩潰,且不在 CI 跑所以沒人發現。
- `tests/test_intent_classifier.py:12` skip 條件檢查 `GOOGLE_API_KEY`,但 `intent_classifier.py:46` 實際用 `OPENROUTER_API_KEY`;`test_intent_classifier.py:162` 還 `match="GOOGLE_API_KEY"`。環境變數名對不上。

---

## 重要(工程品質 / 可維護性)

### 6. 完全沒有 lint / type / format 工具鏈
- 無 `pyproject.toml`、`ruff.toml`、`.flake8`、`mypy.ini`、`.pre-commit-config.yaml`、`Makefile`。整個 repo 沒有任何靜態檢查。
- 業界標準:ruff + mypy + pre-commit 為 Python 專案標配。

### 7. CI 只部署不驗證
- `.github/workflows/deploy.yml` 只有 `deploy` job(build + push + terraform apply),沒有任何 CI workflow 跑 test/lint/typecheck。`master` push 不觸發任何檢查;`release/**` 直接部署。壞程式碼可直接進版。
- 業界標準:PR 必跑 test+lint、typecheck,merge 後再 deploy。

### 8. 依賴幾乎無版本釘選
- `requirements.txt` 34 個套件中只有 4 個有版本限制(`sentence-transformers`、`huggingface_hub`、`ragas`、`datasets`)。其餘 `fastapi`、`langchain`、`pinecone-client`、`gradio` 全裸。建置不可重現、供應鏈風險高。
- 業界標準:`pip-compile`/`uv lock` 產生 lockfile,釘到 hash。

### 9. 測試不 hermetic、無覆蓋率門檻
- 多數測試需外部服務(Pinecone/OpenAI/MongoDB/Redis),`test_intent_classifier.py` 直接打 LLM。無 `conftest.py` 共用 fixture、無 `pytest-cov`、無覆蓋率門檻、無 load/security test。RAGAS 評估腳本存在但未接 CI regression gate。

### 10. `app.py` 637 行的 God Module
- `src/app.py` 把 singletons、embedding/llm/vectorstore factory、reranker、retrieval/generation chain、chat handler、BOM handler、upload handler、Gradio UI、FastAPI mount 全部塞一個檔。`bom_mapper.py:16` 甚至在 module import 時就建構 LLM(副作用)。
- 業界標準:分層(api/service/data)、DI 容器、避免 module-level 副作用。

### 11. 全域可變 singleton,非 thread-safe
- `_reranker`、`_llm`、`_vectorstore`... 用 `global` + `if None` 模式,在並發下有 race condition;無 lifecycle 管理、難 mock。

---

## 中等(可觀測性 / 營運)

### 12. 用 `print()` 而非 `logging`
- 全 repo 65+ 處 `print()`(`app.py` 17、`build_vector_store.py` 25)。無 log level、無 structured log、無 request/correlation ID。Cloud Run 日誌難以過濾警報。LangSmith 只追 RAG 鏈,不涵蓋 BOM/上傳/錯誤。
- 業界標準:`logging` + JSON formatter + trace ID。

### 13. 無健康檢查 / 指標 / 錯誤追蹤
- ~~無 `/health` `/ready` endpoint(Cloud Run 用預設)~~;無 Prometheus 指標(cache hit rate、latency、intent 分類率);無 Sentry/錯誤追蹤;無 alerting。
- **部分完成**:`/health` 已由 `add-access-control` change 補上(豁免認證、不載重型 singleton、Cloud Run liveness 可用)。剩餘:Prometheus 指標、Sentry、alerting 仍未做。

### 14. 外部呼叫無 retry / circuit breaker
- 對 OpenAI、Pinecone、MongoDB、OpenRouter 的呼叫都無重試、無退避、無熔斷。瞬時失敗直接拋給使用者「An error occurred: ...」(還把例外訊息透漏給前端,資訊洩漏)。

### 15. 無 staging 環境、無 rollback
- CI 只能往前部署,無 blue/green、canary、自動 rollback。Terraform foundational infra 靠人手 `apply`。

---

## 輕微(文件 / RAG 品質 / 細節)

### 16. RAG 體驗細節
- `_QA_PROMPT` 無 few-shot、答案不附引用來源(使用者無法驗證)、無 thumbs up/down 回饋機制。
- Semantic cache 是 O(n) 掃全部(上限 5 筆),不會 scale(目前 demo 夠用)。
- BGE reranker 載在 web 行程,`min_instances=0` → 冷啟動重;無 warm pool。

### 17. 文件 / 治理
- 無 `CONTRIBUTING.md`、`SECURITY.md`、`CHANGELOG.md`、`CODEOWNERS`、branch protection。
- FastAPI 已用但 `/docs` OpenAPI 被 Gradio mount 蓋過,API 未對外文件化。
- `docker-compose.yml` 的 `mongo-express` 帳密硬編 `admin/pass`。

### 18. 配置管理
- `src/config.py` 混用 env var 與硬編常數;無 pydantic-settings 在啟動時驗證必填(缺 key 要等到第一次呼叫才爆)。Pinecone index 硬編 `dimension=1536` + `us-east-1`(與 app 的 `asia-east1` 跨區)。

---

## 量化總覽

| 面向 | 現況 | 業界標準 | 差距 |
|---|---|---|---|
| Lint/Type/Format | 無 | ruff + mypy + pre-commit | 🔴 全無 |
| CI 測試門檻 | 無 | PR 必跑 test+lint | 🔴 全無 |
| 依賴鎖定 | 4/34 釘版 | lockfile + hash | 🔴 幾乎全無 |
| 認證/限流 | ~~公開無 auth~~ API key + session + rate limit | IAP/API key + rate limit | ✅ 已完成 |
| 日誌 | print() | structured logging | 🟠 全無 |
| 測試覆蓋率 | 無度量 | ≥70% gate | 🟠 全無 |
| 健康檢查/指標 | `/health` 已補 | /health + Prometheus | 🟡 指標仍無 |
| HA/Scaling | max=1 | ≥2 + autoscale | 🟡 單實例 |

---

## 建議優先順序

1. **馬上**:修 cache key `hash()` bug(#3)、修測試 bug(#5)、~~加認證/限流(#1)~~ ✅、修上傳安全(#2)。
2. **短期**:補 ruff+mypy+pre-commit(#6)、加 CI test workflow(#7)、釘依賴(#8)、`app.py` 拆分(#10)、`print→logging`(#12)。
3. **中期**:retry/circuit breaker(#14)、指標+健康檢查(#13)、staging 環境(#15)、guardrail 強化+對抗測試(#4)。

每個 item 可用 Spectra `/spectra-propose` 走 SDD 流程展開成 change。
