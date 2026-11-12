## Context

目前重排由 web 程序內的 HuggingFaceCrossEncoder 執行 BAAI/bge-reranker-v2-m3：`src/services.py` 的 `get_reranker_model()` 建立單例，`src/rag_pipeline.py` 的重排步驟以 `asyncio.to_thread(rerank_candidates, query, candidates, get_reranker_model().score)` 呼叫。映像檔因此要帶 torch（CPU 版）、sentence-transformers、langchain-huggingface，Dockerfile 在建置時預先下載約 2GB 的模型權重並設定 `HF_HUB_OFFLINE`、`TRANSFORMERS_OFFLINE`；`terraform/main.tf` 的 web 服務為了讓 torch 用滿 4 個 vCPU 設了 `OMP_NUM_THREADS`。

分數的用途有三處：`rerank_candidates` 排序並輸出 `rerank_scores`；`retrieval_grader.grade_documents` 用 top-1 分數和 `GRADE_SCORE_THRESHOLD` 判斷要不要重試、是否低信心（空的 `rerank_scores` 一律不通過）；`chat_service._retrieve_best` 用 top-1 分數決定保留哪一次檢索（沒有分數視為最低）。低信心時 `chat_service` 會把生成用的 context 清空、不送來源、不寫快取。`src/guardrails.py` 的 `check_context_similarity` 也接受一個帶 `score` 的物件，但目前沒有任何呼叫者。

Pinecone Inference 托管同一個模型（每次請求最多 100 筆、每個 query-passage 最多 1024 token、約每千次 $2），專案已有 `PINECONE_API_KEY`。本機沒有 Python 3.13 環境與 pip-tools；現有的 `rag-chatbot-demo-web` image 是 3 週前建的，裡面有完整的依賴（含 torch），但 `.dockerignore` 排除了 `tests/`。

## Goals / Non-Goals

**Goals:**

- 重排改由 Pinecone Inference 執行，映像檔不再帶 torch、sentence-transformers、langchain-huggingface 與模型權重。
- 重排服務失敗或逾時時，請求不失敗：視為低信心、不再重試，沿用既有的低信心行為。
- 排序結果與分數尺度和現在一致；切換前用正式 client 實測確認，`GRADE_SCORE_THRESHOLD` 才沿用。
- 實作後在 demo index 跑 RAGAS，結果不比 tests/rag_eval_report.json 差超過雜訊範圍（0.05），而且評估期間沒有任何重排失敗。

**Non-Goals:**

- 不換成其他模型或供應商（Voyage、Cohere、Vertex AI Ranking）；那需要重新校準門檻，另開 change。
- 不調降 Cloud Run 的 CPU 與記憶體配額；先上線量測，再另外調整。
- 不修改或刪除 `check_context_similarity`（沒有呼叫者，介面是 duck typing，換 client 後仍可用）。
- 不改變重排階段的輸出格式、`RERANK_TOP_K`、`FUSION_TOP_M` 與門檻值。
- 重排失敗時不把融合排序的內容交給 LLM：使用者決定沿用既有低信心行為（不帶 context）。
- 不在對話紀錄新增欄位區分「重排失敗」與「沒有文件」；兩者的 top-1 分數都是 null，排查靠 log 與 LangSmith。
- 不修改未歸檔的 `gcp-deployment` change；它要求 build 時下載模型的需求，要等它歸檔時再處理。
- 不處理 Pinecone 方案與帳單設定。

## Decisions

### 托管重排 client 沿用 get_reranker_model 介面

新增 `src/hosted_reranker.py`，提供一個有 `score(pairs) -> list[float]` 方法的 client；`get_reranker_model()` 改為建立並回傳這個 client（仍是執行緒安全的單例，`PINECONE_API_KEY` 未設定時丟出提到該名稱的 `ValueError`）。這樣 `rag_pipeline` 的注入方式、測試裡的 monkeypatch，以及 `/health` 不載入重量級單例的測試都不用改。

client 用 `requests` 直接呼叫 Pinecone 的 rerank REST 端點，不經過 pinecone SDK。理由：SDK 7.3.0 的 `inference.rerank` 能不能設定單次呼叫的逾時、預設會不會重試，都無法確認；逾時是失敗退回能不能成立的前提。`requests` 已在依賴中，比較腳本也已有相同寫法。請求內容：model `bge-reranker-v2-m3`、query、documents、`top_n` 等於筆數、`return_documents` 為 false、`parameters.truncate` 為 `END`；header 帶 `Api-Key` 與 API 版本。端點網址與 API 版本字串在實作時對照 Pinecone 官方文件確認，寫成模組常數。HTTP 逾時設為 `RERANK_TIMEOUT_SECONDS` 加 1 秒，只送一次、不重試。

`score` 的行為：

- 所有 pair 的 query 必須相同，否則丟出 `ValueError`，不送請求。
- pair 數超過 100（每次請求的上限）丟出 `ValueError`，不送請求。
- 依回傳的 `index` 把分數放回輸入順序；回傳筆數不足、HTTP 狀態不是 200 時丟出錯誤，錯誤訊息不含金鑰。
- 空的 pairs 回傳空 list，不送請求。

替代方案：用 pinecone SDK。缺點如上，逾時與重試行為不可控。

### 重排逾時與失敗退回融合排序

`rag_pipeline` 的重排步驟改成：

1. candidates 為空時，不取得 client、不帶 `rerank_error`，回傳完整的結果 key：空的 `docs`、`rerank_scores`、`contexts`、`sources`，空字串的 `context`，以及原本的 `fusion_metadata`。
2. 否則在 try 區塊內取得 `get_reranker_model()`，再以 `asyncio.wait_for(asyncio.to_thread(rerank_candidates, ...), timeout=RERANK_TIMEOUT_SECONDS)` 呼叫。
3. 逾時（`asyncio.TimeoutError`，以及 client 拋出的 `requests` 逾時）記為 `timeout`；其他例外（含取得 client 失敗）記為 `error`。失敗時回傳 `docs = candidates[:RERANK_TOP_K]`（融合順序）、`rerank_scores = []`、原本的 `fusion_metadata` 與 `rerank_error`；`context`、`contexts`、`sources` 照常由這些文件組出（低信心時 `chat_service` 不會使用它們）。

`RERANK_TIMEOUT_SECONDS` 新增在 `src/config.py`，用環境變數覆寫，無效值記警告並回到預設。逾時後背景執行緒可能還在等 HTTP 回應；client 的 HTTP 逾時限制連線與讀取間隔（不是整個請求的總時間，也不含 DNS），回應是小 JSON，實務上大致在逾時後約 1 秒內結束。

替代方案一：回傳 `error` 事件讓請求失敗。缺點是 Pinecone 短暫故障就讓整個聊天不可用。替代方案二：退回融合排序並當成正常結果。缺點是沒有分數可以判斷品質，可能把不相關內容當成高信心答案寫進快取。

### 重排失敗視為低信心且不重試

`chat_service._retrieve_best` 在任一次執行過的檢索帶 `rerank_error` 時停止重試，`retry_skipped` 維持 false。保留哪一次照既有規則：沒有 top-1 分數的那次永遠不會比較好。第一次就失敗時以低信心結束；`grade_documents` 對空的 `rerank_scores` 本來就回傳不通過，所以生成不帶 context、加固定說明、不送來源、不寫快取都沿用既有邏輯。

### 失敗記錄不含內容

失敗時以 warning 等級記錄失敗種類、例外類別名稱與耗時毫秒數；不記錄 query、passage、例外訊息全文，也不帶 traceback（不使用 `logger.exception` 或 `exc_info`）。LangSmith 的重排 span 名稱改為 "Rerank (Pinecone)"，輸出帶 `rerank_error`。

### 離線評估遇到重排失敗就失敗

`tests/evaluate_rag.py` 直接呼叫檢索 chain 並使用其 context，不經過低信心路徑。改成統計每題 bundle 的 `rerank_error`，有任何一題就印出題數並以 exit code 1 結束，避免失敗被悄悄算進 RAGAS 指標。`scripts/eval_sample_manual.py` 最後呼叫 `evaluate_rag.main()`，所以一併涵蓋。

### 切換前做分數一致性檢查

本機的 HuggingFaceCrossEncoder 對單一輸出的模型會套 sigmoid，分數在 0 到 1 之間；Pinecone 回傳的分數尺度要實測確認，而且要用正式 client（含 `truncate=END`）測，才代表實際上線的行為。

做法：先完成托管 client。把 `chore/rerank-comparison` 分支 commit 413f325 的 scripts/compare_rerankers.py 與其測試帶進來，並做三個修改：

- `pinecone-bge` provider 改用 `src/hosted_reranker.py` 的 client。
- `bge-local` provider 改為直接 import sentence_transformers 建立 CrossEncoder；沒有安裝時視為無法使用並略過，避免在移除 torch 後悄悄變成呼叫 Pinecone。
- 加上一致性報告：以第一個 provider 為基準，列出其他 provider 每個 passage 分數的最大與平均絕對差、top-3 排序不同的題數，以及最長 passage 的字元數。

在移除 torch 之前，用現有 web image 加上掛載的 scripts/、src/、tests/、examples/ 執行：同一個容器內先把範例手冊同步到 demo index 與容器內的 BM25 目錄，再對 synthetic 與 manual 兩個資料集各跑一次並把 JSON 寫到 repo 外。manual 資料集有任何一題的候選為空或退回 `vector_only` 時，腳本以失敗結束，避免沒有比到任何內容卻判定通過。

接受條件：manual 實際比較的題數為 12、每題都有候選且沒有 `vector_only`；最大絕對差不超過 0.02，且 top-3 順序不同的題數為 0（報告另列順序不同但集合相同的題數，供使用者判斷）。不符合時停止實作，回報數據並由使用者決定重新校準門檻或放棄這個 change。

### 測試與重新鎖定依賴在 Python 3.13 容器內執行

本機沒有 Python 3.13 環境。pytest 在移除 torch 前用現有 web image 跑（掛載整個 repo 到另一個目錄執行），移除後用重建的 image 跑；重新鎖定依賴在 python:3.13-slim 容器內安裝 pip-tools 後執行 scripts/compile_requirements.sh，確保鎖定檔對應 Dockerfile 的 Python 版本。

### 依賴與部署設定一起移除

`requirements.in` 移除 torch 釘選與 PyTorch CPU 的 extra-index-url、sentence-transformers、langchain-huggingface，以及只為下載模型才列的 huggingface_hub（它仍是 datasets 的傳遞依賴），再重新產生 `requirements.txt`。Dockerfile 移除 `MODEL_CACHE_DIR`、預先下載模型的步驟、`HF_HUB_OFFLINE` 與 `TRANSFORMERS_OFFLINE`，並更新 CMD 上方提到重排用 `to_thread` 的註解；`terraform/main.tf` 移除 web 服務的 `OMP_NUM_THREADS`（sync job 沒有這個設定）。`src/services.py` 移除只為 torch 選裝置的函式。

`tests/ragas_compat.py` 的註解與 `tests/test_ragas_compat.py` 目前假設要能 import HuggingFaceCrossEncoder，改成只確認 ragas 能 import。`tests/evaluate_rerank.py` 刪除（它用 langchain 的 CrossEncoderReranker，型別不接受托管 client，功能已由比較腳本的 synthetic 資料集取代）。`scripts/real_rag_smoke.py` 本來就呼叫 `get_reranker_model()`，程式不用改。

## Implementation Contract

**可觀察行為：**

- 正常情況：聊天回應的來源、`rerank_scores` 格式與目前相同；分數來自 Pinecone。
- 取得 client 失敗、Pinecone 失敗或逾時：聊天回應仍完成，答案開頭是低信心固定說明，生成不帶檢索內容，沒有 `sources` 事件，不寫入語意快取；對話紀錄的 retrieval 物件 top1_score 為 null、retry_count 0、retry_skipped false；log 有一筆 warning，含失敗種類、例外類別與耗時，不含內容、例外訊息、traceback 與金鑰。
- 檢索沒有候選時不呼叫 Pinecone，也不視為重排失敗。
- web 服務啟動與 `/health` 不載入任何模型權重；`get_reranker_model()` 在 `PINECONE_API_KEY` 未設定時丟出提到該名稱的 `ValueError`。
- Docker image 不含 torch 與模型權重。
- `tests/evaluate_rag.py` 有任何一題重排失敗時以 exit code 1 結束並印出題數。

**驗收：**

- 在 Python 3.13 容器內 `python -m pytest tests -q` 通過，含新的 tests/test_hosted_reranker.py，以及 tests/test_app_pipeline.py、tests/test_chat_service.py、tests/test_services.py、tests/test_config.py、tests/test_compare_rerankers.py 的新案例。
- 分數一致性檢查符合接受條件，數據寫進 `architecture.md` 的評估段。
- demo index 上的 RAGAS 四項指標不低於門檻，與 tests/rag_eval_report.json 的差距在 0.05 內，沒有任何重排失敗，audit index 未變動。

**In scope：** 托管重排 client、重排逾時與失敗退回、低信心不重試、離線評估的失敗偵測、依賴與部署設定、相關測試與文件、分數一致性檢查與 RAGAS。

**Out of scope：** 更換模型或供應商、Cloud Run 資源調降、guardrail 的 `check_context_similarity`、對話紀錄新增欄位、`gcp-deployment` change、Pinecone 方案設定。

## Risks / Trade-offs

- [Pinecone 分數尺度與本機不同] → 切換前用正式 client 做一致性檢查；不符合就停下。
- [Pinecone REST API 的網址或版本字串與預期不同] → 實作時對照官方文件，一致性檢查會實際呼叫一次，有錯就會失敗。
- [每次請求多一次網路往返] → 單次 5 筆候選、資料量小；延遲由比較腳本量測並記入 `architecture.md`。
- [Pinecone 故障時所有 RAG 回答都變低信心、答不出手冊內容] → 使用者接受的降級；答案仍會產生，逾時上限避免請求卡住。
- [逾時後背景執行緒仍佔用] → HTTP 逾時只比 `RERANK_TIMEOUT_SECONDS` 多 1 秒，且不重試。
- [費用] → 約每千次請求 $2，重試會多一次；demo 流量下每月幾美元。
- [移除 torch 後其他套件意外依賴它] → 重新鎖定後在重建的 image 內跑完整測試。

## Migration Plan

1. 在現有 image 上用正式 client 跑分數一致性檢查，確認可以切換。
2. 合併後照常由 `release/**` 部署；正式環境已有 `PINECONE_API_KEY`，不需要新增 secret。
3. 回復：revert 這個 change 的 merge commit 後重新部署，會恢復本機重排、依賴與模型下載步驟。

## Open Questions

（無）
