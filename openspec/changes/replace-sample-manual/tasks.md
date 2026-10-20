## 1. 範例手冊

- [ ] 1.1 （Bundled sample manual）撰寫 `examples/carbonm_sample_manual.xlsx`：約 60 筆自製問答，欄位 `question`、`answer`，帶「問：」「答：」前綴，五類主題各約 12 筆，內容不參考原手冊；驗證：新增 `tests/test_sample_manual.py`，以 `extract_from_xlsx` 解析後至少 50 筆、無殘留前綴、五類關鍵詞皆出現。

## 2. 可設定的 index 名稱

- [ ] 2.1 （Configurable Pinecone index name）`src/config.py` 的 `PINECONE_INDEX_NAME` 改為 `os.getenv("PINECONE_INDEX_NAME") or "rag-demo-qa-index"`；驗證：新增 `tests/test_config.py`，以 reload 測試未設定、覆寫、空字串三個案例。
- [ ] 2.2 `tests/test_document_identity.py` 新增案例：mock Pinecone，`sync_vector_store` 開啟的 index 名稱等於設定值；驗證：pytest 通過。
- [ ] 2.3 `terraform/main.tf` 以單一 local 值在 web Service 與 sync Job 加上 `PINECONE_INDEX_NAME = "carbon-assistant-qa-index"`；驗證：`terraform fmt -check` 與 `terraform validate`（`-backend=false` init）通過，且兩處引用同一 local。
- [ ] 2.4 `docker-compose.yml` 的 web 與 worker 傳入 `PINECONE_INDEX_NAME=${PINECONE_INDEX_NAME:-rag-demo-qa-index}`，`.env.example` 加上說明；驗證：`docker compose config` 未設定時顯示 `rag-demo-qa-index`。
- [ ] 2.5 （腳本先載入 `.env` 再讀設定）`scripts/remove_duplicates_auto.py`、`scripts/detect_and_remove_duplicates.py` 改為先 `load_dotenv()` 再 import `src.config`；驗證：以只在暫存 `.env` 設定 index 的環境執行 import，印出的 index 名稱為 `.env` 中的值（不呼叫 Pinecone）。

## 3. 引用舊手冊的內容

- [ ] 3.1 前端建議問題、`tests/test_query_router.py`、`tests/test_bm25_index.py`、`scripts/real_rag_smoke.py`、`scripts/understand_embeddings.py` 中取自舊手冊的問句改為範例手冊中存在的問句，路由仍判為 rag；驗證：`pytest` 全部通過，前端 `pnpm exec vitest run --maxWorkers=1` 通過。

## 4. 評估資料與 RAGAS

- [ ] 4.1 （Evaluation dataset grounded in the sample manual）`tests/rag_eval_data.json` 改為 12 題，`ground_truth` 逐字取自範例手冊、五類各至少 2 題；驗證：`tests/test_sample_manual.py` 檢查每個 `ground_truth` 都在解析後的範例手冊答案中。
- [ ] 4.2 （Isolated sample evaluation run）新增 `scripts/eval_sample_manual.py`：拒絕正式 index 名稱、同步前後比對正式 index 完整 ID 集合、同步後檢查 demo index 與 BM25 筆數等於手冊筆數、逐題確認 hybrid 檢索沒有 fallback 且命中對應手冊列，再執行 `tests.evaluate_rag`；驗證：以 `PINECONE_INDEX_NAME=carbon-assistant-qa-index` 執行時在任何 Pinecone 呼叫前非零結束。
- [ ] 4.3 （同步與評估在一次性容器內完成）以 `docker run --rm` 用專案 image 執行 `scripts/eval_sample_manual.py`（`PINECONE_INDEX_NAME=rag-demo-qa-index`，`DATA_SOURCE_DIR`、`BM25_INDEX_DIR` 指向容器內 `/tmp` 新目錄，不掛載既有 volume；唯讀掛入 `examples/`、`scripts/`，可寫掛入 `tests/`）；驗證：腳本回報正式 index ID 集合不變、筆數檢查通過，`tests/rag_eval_report.json` 的 `num_cases` 為 12。

## 5. 文件

- [ ] 5.1 `architecture.md` 更新 `PINECONE_INDEX_NAME` 參數與預設值、評估分數與題數、「評估題庫只有 6 題」已知問題，並記錄正式站切換 index 的步驟（web 與 job 同時設定、新資料目錄重新同步、清除語意快取）與既有本機環境的遷移方式；README 快速開始改為上傳 `examples/carbonm_sample_manual.xlsx` 並附遷移提示；驗證：文件數字與 `tests/rag_eval_report.json` 一致。
