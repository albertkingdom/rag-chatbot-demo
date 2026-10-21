## 1. 快取命名空間

- [x] 1.1 （Cache keys are scoped by namespace）`PromptCacheService` 新增 `namespace`（預設 `config.PINECONE_INDEX_NAME`，空字串為不含命名空間的舊格式），資料 key 與索引 key 依命名空間組成，`clear_cache` 只清目前命名空間與 legacy Set，Redis 錯誤時拋出；驗證：`tests/test_cache_service.py` 新增預設命名空間、跨命名空間不命中、只清自己的命名空間、Redis 錯誤拋出等案例，既有案例以 `namespace=""` 維持通過。
- [x] 1.2 （Cache key stability）快取 key 由命名空間前綴加 hash 組成；驗證：`tests/test_cache_service.py` 的 key 穩定性案例以命名空間格式斷言並通過。

## 2. 同步結果

- [x] 2.1 （Clear the semantic cache after a complete sync；同步完成的定義與快取清除）`sync_vector_store()` 在向量與 BM25 都成功時呼叫 `clear_cache` 並印出筆數，BM25 失敗或同步失敗時不清除，清除拋錯只警告；驗證：`tests/test_document_identity.py` 新增四個案例（完整成功清一次、BM25 失敗不清、清除拋錯仍成功、回寫 uuid 失敗不清）並通過。
- [x] 2.2 （Failed sync exits non-zero）`build_vector_store.py` 的 CLI 在 `status=error` 時以結束碼 1 結束；驗證：測試以 mock 讓 `sync_vector_store` 回傳 error，執行 main entry 時結束碼為 1。

## 3. 正式站設定

- [x] 3.0 （BM25 載入 log 立即輸出）`BM25Index._load` 的載入訊息加上 `flush=True`；驗證：`tests/test_bm25_index.py` 以替換 `print` 的方式確認載入訊息含筆數且帶 `flush=True`。

- [x] 3.1 （Configurable Pinecone index name：Production uses its own index and data directories）`terraform/main.tf` 的 `pinecone_index_name` 改為 `carbon-assistant-sample-index`，新增 `knowledge_base_dir`，web 與 Job 的 `DATA_SOURCE_DIR`、`BM25_INDEX_DIR` 改用它；驗證：`terraform fmt -check`、`terraform validate` 通過，web 與 Job 的三個 env 都引用 local 值。
- [x] 3.2 （Isolated sample evaluation run：評估腳本的拒絕清單）`scripts/eval_sample_manual.py` 改為 `PRODUCTION_INDEXES`；驗證：`tests/test_eval_sample_manual.py` 以兩個正式 index 名稱參數化拒絕案例並通過。

## 4. 文件

- [x] 4.1 （切換步驟）`docs/deployment/releases.md` 新增「切換正式站知識庫」操作手冊（含禁止上傳期間、BM25 log 關卡與回復），`architecture.md` 更新正式站 index 與資料目錄、快取命名空間與同步後清快取，移除「手冊同步後不清除語意快取」已知問題、更新「正式 index 名稱寫在兩處」，並新增「同一 index 更新後短暫寫入舊快取」已知問題；驗證：文件中的名稱、目錄、log 字串與程式及 Terraform 一致，pytest 全部通過。

## 5. 切換（與使用者一起執行）

- [x] 5.1 確認 Pinecone 方案可再建立一個 index；驗證：使用者在 Pinecone 主控台確認（2026-10-21，使用者確認可再建立）。
- [ ] 5.2 依 `docs/deployment/releases.md` 的步驟發布並切換；驗證：Job log 有 `Upserted: 60` 與 `BM25 index rebuilt with 60 documents`，新 revision log 有 `Loaded persisted index (60 docs) from /mnt/data/sample/models/`，preview 與正式 URL 的回答來源為範例手冊，`carbon-assistant-qa-index` 向量數仍為 126。
