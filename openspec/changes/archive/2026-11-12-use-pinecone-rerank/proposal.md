## Why

重排器 BAAI/bge-reranker-v2-m3 目前跑在 web 程序裡（HuggingFaceCrossEncoder），映像檔因此要帶 torch、sentence-transformers 和約 2GB 的模型權重。Cloud Run 縮到零之後，第一個請求要等模型載入；而且只能用 CPU 跑，重排是每次 RAG 請求延遲的主要來源之一。Pinecone Inference 托管了同一個模型，專案本來就有 Pinecone 金鑰，換過去可以拿掉這些負擔，排序結果在理論上維持不變。

## What Changes

- 檢索 chain 的重排改成呼叫 Pinecone Inference 托管的 bge-reranker-v2-m3（直接呼叫 REST API，明確設定逾時、不自動重試）；重排階段的介面與輸出格式（`docs`、`rerank_scores`）不變。
- 新增重排失敗時的行為：取得 client 失敗、Pinecone 呼叫失敗或逾時時，請求不失敗，這次檢索視為低信心且不再重試。依既有的低信心行為，答案不帶檢索內容，開頭加上固定說明，不顯示來源，不寫入快取。失敗會記錄種類、例外類別與耗時（不含內容、例外訊息與金鑰）。
- 重排呼叫有逾時上限（新參數放在 `src/config.py`）。
- 離線評估遇到重排失敗時讓腳本失敗，避免失敗被悄悄算進 RAGAS 指標。
- 拿掉 torch、sentence-transformers、langchain-huggingface 和映像檔內預先下載的模型權重，以及只為本機模型存在的 `OMP_NUM_THREADS`、`HF_HUB_OFFLINE`、`TRANSFORMERS_OFFLINE` 設定。**BREAKING**（部署層面）：web 服務需要 `PINECONE_API_KEY` 才能重排；正式環境本來就有這把金鑰。
- 切換前驗證分數尺度：同一批 (query, passage) 在本機模型與正式要用的 Pinecone client 上的分數差距要在允許範圍內，`GRADE_SCORE_THRESHOLD` 才沿用；否則停下，由使用者決定。

## Capabilities

### New Capabilities

（無）

### Modified Capabilities

- `rerank-stage`：注入的 scorer 改為托管重排服務的 client（不重試、有 HTTP 逾時）；以「單次呼叫的候選數與逾時上限」取代 `OMP_NUM_THREADS` 的資源需求；新增重排失敗時退回融合排序的需求。
- `low-confidence-handling`：「沒有 top-1 分數」的定義涵蓋重排失敗；新增「重排失敗時視為低信心、不再重試」的需求。
- `app-structure`：執行緒安全單例的情境改為描述重排服務的 client 並補上缺金鑰的情境；本機 web 與 worker 共用資料的需求不再提到模型快取。

## Impact

- Affected specs: `rerank-stage`、`low-confidence-handling`、`app-structure`
- Affected code:
  - New: src/hosted_reranker.py、tests/test_hosted_reranker.py、tests/test_evaluate_rag.py、scripts/compare_rerankers.py、tests/test_compare_rerankers.py（後兩者從 chore/rerank-comparison 分支帶入）
  - Modified: src/services.py、src/rag_pipeline.py、src/chat_service.py、src/config.py、Dockerfile、terraform/main.tf、requirements.in、requirements.txt、architecture.md、README.md、tests/test_app_pipeline.py、tests/test_services.py、tests/test_chat_service.py、tests/test_config.py、tests/test_ragas_compat.py、tests/ragas_compat.py、tests/evaluate_rag.py
  - Removed: tests/evaluate_rerank.py（由比較腳本的 synthetic 資料集取代）
- 外部服務：Pinecone Inference，約每千次請求 $2；每次 RAG 請求重排 1 次，重試時再 1 次。
- 品質驗證：實作後在 demo index 跑 RAGAS（eval-rag），並與 tests/rag_eval_report.json 比較。
- 已知衝突：未歸檔的 `gcp-deployment` change 有一條需求要求在 build 時下載 BGE 模型，與這次的 change 相反；那個 change 之後要歸檔時，要先拿掉那條需求。
