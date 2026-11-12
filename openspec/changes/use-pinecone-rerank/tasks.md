## 1. 測試與重新鎖定依賴在 Python 3.13 容器內執行

- [ ] 1.1 確認測試環境：在移除 torch 之前，用現有 image 掛載 repo 執行 `docker run --rm -v "$PWD":/src -w /src rag-chatbot-demo-web:latest python -m pytest tests -q`，記錄基準結果（通過數、skip 數）。驗證：命令成功結束，結果寫進這次工作的回報；image 不存在或測試無法執行時，先 `docker compose build web` 再跑。

## 2. 托管重排 client 沿用 get_reranker_model 介面

- [ ] 2.1 新增 src/hosted_reranker.py 的托管重排 client：`score(pairs)` 以 `requests` 呼叫 Pinecone rerank REST 端點（model `bge-reranker-v2-m3`、`top_n` 等於筆數、`return_documents` false、`truncate` END、header 帶 `Api-Key` 與 API 版本、HTTP 逾時為 `RERANK_TIMEOUT_SECONDS` 加 1 秒、只送一次不重試），依回傳 index 把分數放回輸入順序；query 不一致或超過 100 筆時丟出 `ValueError` 且不送請求；空 pairs 回傳空 list；HTTP 非 200 或回傳筆數不足時丟出不含金鑰的錯誤。端點網址與 API 版本對照 Pinecone 官方文件後寫成模組常數。先在 src/config.py 新增 `RERANK_TIMEOUT_SECONDS`（環境變數覆寫，無效值記警告並回到預設）。驗證：tests/test_hosted_reranker.py 以 patch 過的 `requests.post` 涵蓋重新排序、query 不一致、超過上限、空輸入、HTTP 401 只送一次且訊息不含金鑰、回傳筆數不足、請求內容與 timeout 參數，對應「Rerank scorer is injected as a callable」與「Rerank stage is resourced for bounded inference latency」；tests/test_config.py 新增預設值、有效覆寫、無效值三個案例；全部在 1.1 的命令下通過。

## 3. 切換前做分數一致性檢查

- [ ] 3.1 把 `chore/rerank-comparison` 分支 commit 413f325 的 scripts/compare_rerankers.py 與 tests/test_compare_rerankers.py 帶進這個分支，並修改：`pinecone-bge` 改用 src/hosted_reranker.py 的 client；`bge-local` 改為直接 import sentence_transformers 建立 CrossEncoder（cache 目錄讀 `MODEL_CACHE_DIR`），沒有安裝時拋出 `ProviderUnavailable` 而略過；新增一致性報告（以第一個 provider 為基準，輸出其他 provider 每個 passage 分數的最大絕對差、平均絕對差、top-3 順序不同的題數、top-3 順序不同但集合相同的題數、實際比較的題數與 passage 數，以及最長 passage 字元數，寫進終端表格與 `--out` 的 JSON）；manual 資料集有任何一題的候選為空，或 `fusion_metadata` 的 fallback 為 `vector_only`，就印出題數並以 exit code 1 結束；報告註明 bge-local 使用的 activation（單一輸出時為 sigmoid，與 HuggingFaceCrossEncoder 相同）。驗證：tests/test_compare_rerankers.py 新增案例（兩個假 scorer 差 0.01 時最大差為 0.01、順序不同時計數為 1、集合相同時另計、sentence_transformers 無法 import 時 bge-local 被略過、manual 有空候選或 vector_only 時回傳 1）在 1.1 的命令下通過。
- [ ] 3.2 用現有 image 在同一個容器裡先把範例手冊同步到 demo index 與容器內的 BM25 目錄，再跑一致性檢查（同步會呼叫 OpenAI embeddings 並改寫 demo index，執行前取得使用者同意）：`docker run --rm --env-file .env -e PINECONE_INDEX_NAME=rag-demo-qa-index -e DATA_SOURCE_DIR=/tmp/sample/uploaded_files -e BM25_INDEX_DIR=/tmp/sample/bm25 -v "$PWD"/src:/app/src -v "$PWD"/scripts:/app/scripts -v "$PWD"/tests:/app/tests -v "$PWD"/examples:/app/examples -v "$SCRATCH":/out rag-chatbot-demo-web:latest sh -c "mkdir -p /tmp/sample/uploaded_files && cp examples/carbonm_sample_manual.xlsx /tmp/sample/uploaded_files/ && python -m src.build_vector_store && python -m scripts.compare_rerankers --dataset synthetic --providers bge-local,pinecone-bge --out /out/parity-synthetic.json && python -m scripts.compare_rerankers --dataset manual --providers bge-local,pinecone-bge --out /out/parity-manual.json"`，`$SCRATCH` 是 repo 外的暫存目錄。同步若有新寫入的向量，先確認 demo index 的向量數等於手冊筆數再比較（Pinecone 寫入有延遲）。驗證：同步與兩次比較都以 exit code 0 結束；manual 報告的比較題數為 12、每題都有候選且沒有 vector_only；兩份報告的最大絕對差都不超過 0.02，且 top-3 順序不同的題數為 0。不符合時停止實作，把兩份報告的數據（含「順序不同但集合相同」的題數）回報給使用者決定。

## 4. 托管 client 的執行緒安全單例

- [ ] 4.1 `get_reranker_model()` 改為建立托管 client 的單例，`PINECONE_API_KEY` 未設定時丟出提到該名稱的 `ValueError`；移除只為 torch 選裝置的函式。驗證：tests/test_services.py 的並行初始化案例改斷言 client 只建立一次，新增缺金鑰案例，兩者都用 monkeypatch 還原單例的 `_build` 與快取值，依「Thread-safe singleton providers」通過；tests/test_health_endpoint.py 不修改並通過。

## 5. 重排逾時與失敗退回融合排序

- [ ] 5.1 `rag_pipeline` 的重排步驟：candidates 為空時直接回傳空結果，不取得 client；否則在 try 內取得 `get_reranker_model()`，以 `RERANK_TIMEOUT_SECONDS` 為上限呼叫 `rerank_candidates`；逾時（含 client 的 HTTP 逾時）記為 `timeout`，其他例外（含取得 client 失敗）記為 `error`；失敗時回傳融合順序的前 `RERANK_TOP_K` 筆、空的 `rerank_scores`、原本的 `fusion_metadata` 與 `rerank_error`；成功時不帶 `rerank_error`；span 名稱改為 "Rerank (Pinecone)"。驗證：tests/test_app_pipeline.py 新增「scorer 丟例外」「取得 client 失敗」「scorer 超過逾時（用 `threading.Event` 阻擋並在測試結束時放行，`RERANK_TIMEOUT_SECONDS` 以 monkeypatch 調小）」「沒有候選時不取得 client」「成功時沒有 rerank_error」五個案例，依「Rerank failure falls back to fused order」通過。
- [ ] 5.2 失敗記錄不含內容：重排失敗時以 warning 記錄失敗種類、例外類別名稱與耗時，不記錄 query、passage、例外訊息與 traceback。驗證：tests/test_app_pipeline.py 用 caplog 斷言紀錄為 WARNING、含 `timeout`／`error` 與例外類別名稱、`record.exc_info is None`，且不含測試用的 query、passage 與例外訊息文字。

## 6. 重排失敗視為低信心且不重試

- [ ] 6.1 `chat_service._retrieve_best` 在任一次執行過的檢索帶 `rerank_error` 時停止重試，`retry_skipped` 維持 false。驗證：tests/test_chat_service.py 新增三個案例並通過：第一次失敗時不呼叫 `rewrite_for_retry`、生成的 context 為空、答案開頭是固定說明、沒有 sources 事件、不寫入快取，retrieval 紀錄為 top1_score null、attempt_scores [null]、retry_count 0、kept_attempt 0、retry_skipped false（對應「Rerank failure is treated as low confidence without retry」「Low-confidence answers are labelled and carry no sources」「Retrieval confidence is recorded with each RAG conversation」）；`RETRIEVAL_MAX_RETRIES=2`、第一次 0.10 而第一次重試失敗時，不做第二次重試且保留第一次（對應「Retry keeps the better retrieval」）；既有低信心測試全部通過。

## 7. 離線評估遇到重排失敗就失敗

- [ ] 7.1 tests/evaluate_rag.py 在 `collect_samples` 之後、`score_with_ragas` 之前統計每題檢索結果的 `rerank_error`，有任何一題就印出題數、不寫報告檔並以 exit code 1 結束。驗證：tests/test_evaluate_rag.py 新增測試，以 monkeypatch 設定假的評分憑證、讓 `collect_samples` 回傳一題帶 `rerank_error` 的結果，斷言 `main()` 回傳 1、輸出含題數、`score_with_ragas` 沒有被呼叫；沒有失敗時 `score_with_ragas` 照常被呼叫；在 1.1 的命令下通過。

## 8. 依賴與部署設定一起移除

- [ ] 8.1 `requirements.in` 移除 torch 釘選與 PyTorch CPU extra-index-url、sentence-transformers、langchain-huggingface、huggingface_hub，在 python:3.13-slim 容器內執行 `docker run --rm -v "$PWD":/src -w /src python:3.13-slim sh -c "pip install -q pip-tools && scripts/compile_requirements.sh"` 重新產生 requirements.txt，兩個檔案一起 commit。驗證：requirements.txt 不再出現 torch、sentence-transformers、langchain-huggingface。
- [ ] 8.2 Dockerfile 移除 `MODEL_CACHE_DIR`、預先下載模型的步驟、`HF_HUB_OFFLINE` 與 `TRANSFORMERS_OFFLINE`，並改寫 CMD 上方提到重排用 `to_thread` 的註解；terraform/main.tf 移除 web 服務的 `OMP_NUM_THREADS`。驗證：`docker compose build web worker` 成功；`docker run --rm rag-chatbot-demo-web:latest python -c "import torch"` 失敗；`cd terraform && terraform fmt -check -recursive && terraform init -backend=false -input=false && terraform validate` 通過；docker-compose 的 web 與 worker 仍共用上傳與 BM25 volume（對應「Local web and worker share synchronization data」）。
- [ ] 8.3 tests/ragas_compat.py 的註解與 tests/test_ragas_compat.py 改成只確認 ragas 能 import；刪除 tests/evaluate_rerank.py。驗證：在 8.2 重建的 image 內執行 `docker run --rm -v "$PWD":/src -w /src rag-chatbot-demo-web:latest python -m pytest tests -q` 通過，且 `grep -rn HuggingFaceCrossEncoder src tests scripts` 沒有結果。

## 9. 文件與評估

- [ ] 9.1 更新 architecture.md：重排段（托管服務、REST 呼叫、逾時、失敗退回與低信心不重試、失敗記錄）、Key Parameters 加入 `RERANK_TIMEOUT_SECONDS`、評估段記錄 3.2 的一致性數據與延遲，並加入 413f325 對比較腳本的說明；README.md 中提到本機 BGE 重排的地方同步更新。驗證：內容檢查，`grep -n "OMP_NUM_THREADS\|HuggingFaceCrossEncoder\|MODEL_CACHE_DIR" architecture.md README.md` 沒有結果。
- [ ] 9.2 依 eval-rag 在 demo index 跑 scripts/eval_sample_manual.py，用 8.2 重建的 image 執行（會呼叫付費服務並改寫 demo index，執行前取得使用者同意）：`docker run --rm --env-file .env -e PINECONE_INDEX_NAME=rag-demo-qa-index -e DATA_SOURCE_DIR=/tmp/sample/uploaded_files -e BM25_INDEX_DIR=/tmp/sample/bm25 -v "$PWD":/src -w /src rag-chatbot-demo-web:latest python -m scripts.eval_sample_manual --audit-index carbon-assistant-sample-index`。這會覆寫已追蹤的 tests/rag_eval_report.json，是否 commit 依 eval-rag 的步驟 7 決定。與 tests/rag_eval_report.json 的已 commit 版本比較。驗證：腳本沒有因重排失敗而結束，四項指標都不低於門檻，與基準差距在 0.05 內，audit index 未變動；退化超過 0.05 時重跑一次，仍退化就停下回報。
