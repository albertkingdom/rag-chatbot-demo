## Context

知識庫與評估資料目前來自他方提供的手冊（question/answer 兩欄 XLSX），repo 已公開。Pinecone index 名稱寫死在 `src/config.py` 的 `PINECONE_INDEX_NAME`，本機與正式環境用同一個 Pinecone 帳號與 index。手冊同步（`sync_vector_store`）會列出 index 內全部 ID，upsert 本地有而 index 沒有的，並刪除 index 有而本地沒有的；同步結束才重建 BM25，且 BM25 建置失敗時仍回傳成功。正式站的 web Service 與 sync Job 由 Terraform 設定環境變數，兩者共用 GCS 上的 `DATA_SOURCE_DIR` 與 `BM25_INDEX_DIR`。語意快取的 key 不含 index 名稱。RAGAS 評估（`tests/evaluate_rag.py`）直接呼叫 hybrid 檢索與生成 chain，不經過語意快取。

## Goals / Non-Goals

**Goals:**

- 提供自製、可公開的範例手冊，讓他人照 README 即可建立知識庫。
- 預設設定下，任何本機同步都不會寫入正式 index。
- 評估資料與報告改以範例手冊為依據，題數 12 題，並證明評估確實使用新 index 與新 BM25。

**Non-Goals:**

- 不更名產品、不改路由關鍵字語意、不改檢索、同步與評估流程本身的行為。
- 不切換正式站的知識庫，也不修改 GCS 上的手冊與 BM25 索引。
- 不為語意快取加入 index 命名空間。
- 不清理 git 歷史。

## Decisions

### 預設 index 改為 demo，正式站由 Terraform 明確指定

`src/config.py` 改為 `PINECONE_INDEX_NAME = os.getenv("PINECONE_INDEX_NAME") or "rag-demo-qa-index"`，未設定與空字串都回到預設值。`terraform/main.tf` 在 web Service 與 sync Job 的 container env 都加上 `PINECONE_INDEX_NAME = "carbon-assistant-qa-index"`，以同一個 local 值定義，保證兩者一致且正式行為不變。`docker-compose.yml` 的 web 與 worker 傳入 `PINECONE_INDEX_NAME=${PINECONE_INDEX_NAME:-rag-demo-qa-index}`。`.env.example` 說明此變數與「勿設為正式 index」。

替代方案「預設值維持正式 index、本機另行覆寫」被否決：使用正式 Pinecone 金鑰的本機環境一旦忘記覆寫，同步就會刪除正式 index 中不在新手冊的向量。

部署順序：config 與 Terraform 必須在同一個 commit 一起發布；在下一次 release 之前，正式站仍執行舊 image（寫死正式 index），不受影響。

### 腳本先載入 `.env` 再讀設定

`scripts/remove_duplicates_auto.py` 與 `scripts/detect_and_remove_duplicates.py` 目前在 `load_dotenv()` 之前就 import `PINECONE_INDEX_NAME`。改為在模組頂端先呼叫 `load_dotenv()`，再 import `src.config`，讓 `.env` 中的 index 設定生效。

### 同步與評估在一次性容器內完成

新增 `scripts/eval_sample_manual.py`，在同一個程序內依序：

1. 檢查 `src.config.PINECONE_INDEX_NAME` 不等於 `carbon-assistant-qa-index`、也不等於 `--audit-index`，並確認三組憑證都有值，否則在呼叫 Pinecone 前中止。
2. 有指定 `--audit-index` 時，記錄該 index 的完整 ID 集合（只讀 `list`）。未指定就不讀任何其他 index，讓沒有正式 index 的公開使用者也能執行。
3. 把 `examples/carbonm_sample_manual.xlsx` 複製到 `DATA_SOURCE_DIR` 後執行 `sync_vector_store()`。
4. 確認 demo index 的 ID 數等於手冊筆數，並載入 `BM25_INDEX_DIR` 的索引，確認文件數也等於手冊筆數；任一不符即中止。
5. 對每一題評估問題執行 hybrid 檢索，`fusion_metadata` 不得含 `fallback`、`bm25_results` 不得為空，任一題不符即中止；另外列出 `ground_truth` 對應的手冊列（以 doc_id 比對）有出現在 `fusion_results` 中的題數。未命中屬於檢索品質，由 RAGAS 的 context_recall 衡量，不作為中止條件。
6. 執行 `tests.evaluate_rag` 的主流程，寫出報告。
7. 有指定 `--audit-index` 時，在 `finally` 中再次讀取 ID 集合並與第 2 步比對，必須完全相同；第 3 到 6 步提早失敗或拋出例外時也會比對，原本的例外照常往外拋。

範例手冊附固定 uuid（以固定 namespace 對問題文字做 uuid5），重新同步時 ID 不變，不會刪除並重新 embedding 全部資料。

以 `docker run --rm` 使用專案 image 執行，環境變數為 `PINECONE_INDEX_NAME=rag-demo-qa-index`、`DATA_SOURCE_DIR` 與 `BM25_INDEX_DIR` 指向容器內 `/tmp` 下的新目錄，不掛載既有 volume。專案 image 透過 `.dockerignore` 排除了 `tests/`，因此以唯讀掛入 `examples/` 與 `scripts/`，並把 repo 的 `tests/` 以可寫方式掛到 `/app/tests`，讓評估程式、題庫與 `ragas_compat.py` 可用，報告直接寫回 `tests/rag_eval_report.json`。

### 範例手冊內容

自行撰寫約 60 筆，欄位 `question`、`answer`，沿用「問：」「答：」前綴以符合 `extract_from_xlsx` 的解析。五類主題各約 12 筆：帳號與登入、盤查邊界與多廠區、排放源與排放係數、數據條目與審核、報表與清冊。內容為通用碳盤查知識與虛構的 CarbonM 操作流程，不參考原手冊文字，並涵蓋前端建議問題與路由測試會用到的主題。

### 評估資料

12 題，每題 `ground_truth` 逐字取自範例手冊某一筆 `answer`，`question` 為口語改寫；五類各至少 2 題。

### 既有本機環境的遷移

預設 index 改變後，既有本機 Compose 環境會改用空的 demo index，但 `manual_data`、`bm25_data` volume 與 Redis 語意快取仍保留舊資料。README 與 architecture.md 說明遷移方式：要沿用舊資料就在 `.env` 設定原本的 index；要改用範例手冊，因介面沒有刪除手冊的功能，需以 `docker compose down -v` 清空本機資料後重新啟動，再上傳範例手冊並等待同步。

## Risks / Trade-offs

- [正式站日後切換 index 時，語意快取可能回傳舊知識庫的答案] → architecture.md 記錄切換步驟：web 與 job 同時設定 index、使用新的資料目錄並重新同步、清除語意快取。
- [12 題仍偏少，LLM 評分有隨機性] → 報告註明題數，architecture.md 的已知問題改寫為 12 題的限制。
- [同步與評估呼叫付費 API] → 約 60 筆 embedding 與 12 題評分，成本小。
- [Terraform 新增 env 需經 release 才生效] → 值與現行寫死的名稱相同，release 前後行為一致。
