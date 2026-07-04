# 業界標準差距分析

> 對照業界標準,本專案目前的差距清單。建立日期:2026-07-01。
> Item #1 已完成(commit `a08d9c0`,branch `feature/auth-and-rate-limit`)。
> Item #3 已完成(commit `14b71a5`,branch `fix-cache-key-hash`)。

## 嚴重(安全 / 正確性,應優先處理)

### 1. 應用程式完全無認證,且 Cloud Run 對全網公開
- ~~`terraform/main.tf` 的 `google_cloud_run_v2_service_iam_member.public` 將 Service 開放給 `allUsers`,`src/app.py` 無任何 auth/rate-limit。任何能連網的人都能呼叫 LLM/Pinecone/OpenAI,直接產生費用與濫用風險。~~
- 業界標準:至少加 API key / IAP / Cloud Run IAM 限縮 + rate limit。
- **狀態:已完成(commit `a08d9c0`)** — Spectra change `add-access-control` 已 archived:Cloud Run IAM 從 `allUsers` 改為 `allowed_invoker_members` 限縮;app 層 `src/access_control.py` 加入 API key header + Redis-backed session token(`/login`/`/logout`)+ per-key fixed-window 限流 + 豁免認證的 `/health`;Terraform `APP_API_KEY` 注入 Secret Manager;34 tests hermetic 全綠。

### 2. 檔案上傳有路徑穿越風險
- ~~`src/app.py` 的 `upload_manual_func` 與 `bom_mapper_func` 直接用 `file.name`~~;已改用 `Path(file.name).name` 防止路徑穿越。仍缺:檔案大小限制、content-type 驗證、掃毒。
- **狀態:部分完成** — 路徑穿越已修複,仍需補上 size cap + content-type 驗證。

### 3. Cache key 用 Python 內建 `hash()`,跨行程不穩定
- ~~`src/cache_service.py:147` `abs(hash(tuple(question_embedding[:10])))`。Python 預設啟用 hash randomization(`PYTHONHASHSEED`),同一 embedding 在不同 worker 行程會算出不同 key,導致 cache 永遠 miss、且 index 與 entry 對不上。另外只取前 10 維易碰撞。~~
- **狀態:已完成(commit `14b71a5`)** — Spectra change `fix-cache-key-hash` 已 archived:改用 `hashlib.sha256(struct.pack(...))` 對完整 1536 維 embedding 計算 SHA256,取 16 字元 hex 作為 key；加入 `redis.exists()` 存在性檢查避免重複 cache 時 `hit_count` 歸零；新增碰撞測試與穩定性測試；修復兩個 pre-existing 測試 bug（pipeline mock 問題）；16 tests 全綠。

### 4. Guardrails 是脆弱的正則,易被繞過
- `src/guardrails.py` 用關鍵字 regex。同義改寫、Base64、拆字、英文混中文皆可繞過;且把 `password`/`token`/`secret` 列為 injection 會誤殺合法的「忘記密碼」問題(正好是 README 的範例問題)。
- 業界標準:用專用偵測器(Lakera Guard / 自訂分類器)+ 評測對抗樣本。

### 5. 測試本身有 bug,保護網失靈
- ~~`tests/test_rag_stream.py:14` `from app import rag_chain`~~ — **已修復**:改為 `get_generation_chain()` from `src.rag_pipeline`。
- `tests/test_intent_classifier.py:12` skip 條件檢查 `GOOGLE_API_KEY`,但 `intent_classifier.py:45` 實際用 `OPENROUTER_API_KEY`。環境變數名對不上,仍需修複。

---

## 重要(工程品質 / 可維護性)

### 6. 完全沒有 lint / type / format 工具鏈
- **狀態:仍未完成** — 無 `pyproject.toml`、`ruff.toml`、`.flake8`、`mypy.ini`、`.pre-commit-config.yaml`。整個 repo 沒有任何靜態檢查。
- 業界標準:ruff + mypy + pre-commit 為 Python 專案標配。

### 7. CI 只部署不驗證
- **狀態:仍未完成** — `.github/workflows/deploy.yml` 只有 `deploy` job(build + push + terraform apply),無 test/lint/typecheck workflow。`release/**` 直接部署,缺少:PR test gate、lint gate。
- 業界標準:PR 必跑 test+lint、typecheck,merge 後再 deploy。

### 8. 依賴幾乎無版本釘選
- ~~`requirements.txt` 34 個套件中只有 4 個有版本限制(`sentence-transformers`、`huggingface_hub`、`ragas`、`datasets`)。其餘 `fastapi`、`langchain`、`pinecone-client`、`gradio` 全裸。建置不可重現、供應鏈風險高。~~
- **狀態:已完成** — `requirements.txt` 由 `pip-compile --generate-hashes` 生成，所有 34 個套件已釘選到 hash 級別，確保建置可重現。

### 9. 測試不 hermetic、無覆蓋率門檻
- 多數測試需外部服務(Pinecone/OpenAI/MongoDB/Redis),`test_intent_classifier.py` 直接打 LLM。無 `conftest.py` 共用 fixture、無 `pytest-cov`、無覆蓋率門檻、無 load/security test。RAGAS 評估腳本存在但未接 CI regression gate。

### 10. `app.py` 637 行的 God Module
- ~~`src/app.py` 把 singletons、embedding/llm/vectorstore factory、reranker、retrieval/generation chain、chat handler、BOM handler、upload handler、Gradio UI、FastAPI mount 全部塞一個檔。~~
- **狀態:已完成** — 已拆分成:
  - `src/services.py` (15 KB):singletons + factories(LazySingleton 實現 thread-safe double-checked locking)
  - `src/rag_pipeline.py` (15 KB):retrieval/generation chain
  - `src/ui.py` (7 KB):Gradio UI、BOM handler、upload handler
  - `src/access_control.py` (14 KB):FastAPI auth middleware、/health、/login、/logout
  - `src/app.py` (1.2 KB):只負責 mount auth + Gradio、re-export 相容性 import

### 11. 全域可變 singleton,非 thread-safe
- ~~`_reranker`、`_llm`、`_vectorstore`... 用 `global` + `if None` 模式~~
- **狀態:已完成** — `src/services.py` 實現 `LazySingleton` 類，使用 double-checked locking:
  - 並發下初始化恰好一次,之後無鎖
  - 避免 module-level 副作用(build 函數都是無狀態的)
  - 6 個 pure provider(redis、embeddings、llm、vectorstore、hybrid_retriever、reranker)共享實現;特殊邏輯(bm25 reload、intent_classifier None-on-missing)手寫

---

## 中等(可觀測性 / 營運)

### 12. 用 `print()` 而非 `logging`
- **狀態:部分完成** — 48 處 `print()`(bom_mapper.py 11、build_vector_store.py 25、cache_service.py 5、bm25_index.py 6):
  - ✅ 3 個核心模塊已遷移:`src/services.py`、`src/rag_pipeline.py`、`src/access_control.py` 使用 `logging.getLogger("module_name")`
  - ❌ 周邊工具模塊(build_vector_store、bom_mapper、cache_service、bm25_index)仍用 `print()`,應遷移到 `logging`

### 13. 無健康檢查 / 指標 / 錯誤追蹤
- ~~無 `/health` `/ready` endpoint(Cloud Run 用預設)~~;~~無 Prometheus 指標(cache hit rate、latency、intent 分類率)~~;無 Sentry/錯誤追蹤;無 alerting。
- **部分完成**:~~`/health` 已由 `add-access-control` change 補上(豁免認證、不載重型 singleton、Cloud Run liveness 可用)~~。剩餘:Sentry、alerting 仍未做。

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
| 依賴鎖定 | ~~4/34~~ **34/34 釘版(hash)** | lockfile + hash | ✅ 已完成 |
| 認證/限流 | ~~公開無 auth~~ API key + session + rate limit | IAP/API key + rate limit | ✅ 已完成 |
| 模塊結構 | ~~app.py 637 行~~ 已拆分 | 分層 + DI | ✅ 已完成 |
| Singleton 安全 | ~~全域 race~~ LazySingleton | thread-safe | ✅ 已完成 |
| 日誌 | 48 print() + 部分 logging | structured logging | 🟠 部分完成 |
| 測試覆蓋率 | 無度量 | ≥70% gate | 🟠 全無 |
| Cache key 穩定性 | ~~hash()~~ SHA256 全維 | 加密 hash 跨行程一致 | ✅ 已完成 |
| 健康檢查/指標 | `/health` 已補 | /health + Prometheus | 🟡 指標仍無 |
| 檔案安全 | ~~路徑穿越~~ 已修 | size + content-type | 🟡 部分完成 |
| HA/Scaling | max=1 | ≥2 + autoscale | 🟡 單實例 |

---

## 建議優先順序

1. **馬上**:~~修 cache key `hash()` bug(#3)~~ ✅、修測試 bug(#5)、~~加認證/限流(#1)~~ ✅、修上傳安全(#2)。
2. **短期**:補 ruff+mypy+pre-commit(#6)、加 CI test workflow(#7)、釘依賴(#8)、`app.py` 拆分(#10)、`print→logging`(#12)。
3. **中期**:retry/circuit breaker(#14)、指標+健康檢查(#13)、staging 環境(#15)、guardrail 強化+對抗測試(#4)。

每個 item 可用 Spectra `/spectra-propose` 走 SDD 流程展開成 change。
