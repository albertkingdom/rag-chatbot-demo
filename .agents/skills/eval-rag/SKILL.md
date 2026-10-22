---
name: eval-rag
description: "在 demo index 上跑這個專案的 RAGAS 評估並和上一份報告比較：判斷變更需不需要評估、安全前置檢查（絕不對正式 index 執行）、執行 scripts/eval_sample_manual.py、比較四項指標與門檻、解讀結果與雜訊、決定要不要更新 tests/rag_eval_report.json 與文件裡的數字。當使用者說「跑評估」「跑 RAGAS」「評估一下檢索」「改完參數要不要重新評估」「比較評估結果」，或改了檢索、路由、重排、評分門檻、提示詞、模型設定、知識庫同步後需要驗證品質時使用。"
---

# RAGAS 評估與前後比較

評估會呼叫付費服務（OpenRouter、OpenAI、Pinecone），而且**會把範例手冊同步到 demo index，同步會刪除 index 中不在本地手冊裡的向量**。所以跑之前要先確認目標，**經使用者同意才執行**。CI 不跑這個（`docs/development.md`）。

## 邊界

- **絕不對正式 index 執行。** 正式 index 是 `carbon-assistant-sample-index`；`carbon-assistant-qa-index` 是保留供回復的舊 index（見 `architecture.md` 的知識庫同步段與 `terraform/main.tf`）。腳本自己也會拒絕 `PRODUCTION_INDEXES` 裡的名稱，但名稱寫在兩處，改正式 index 時兩處都要改，不能只靠腳本擋。
- 不印出、不記錄任何金鑰。
- 不為了讓數字好看而改題庫、門檻或評估腳本；任何調整都要寫進報告說明。
- 評估結果只描述這個題庫（12 題），不能寫成一般準確率。

## 步驟

### 1. 判斷要不要跑

| 變更 | 要評估嗎 |
|---|---|
| 檢索與排序：`VECTOR_TOP_N`、`BM25_TOP_N`、`RRF_K`、`FUSION_TOP_M`、`RERANK_TOP_K`、BM25 斷詞、重排模型 | 要 |
| 評分與重試：`GRADE_SCORE_THRESHOLD`、`RETRIEVAL_MAX_RETRIES`、改寫查詢的提示詞 | 要（注意 `evaluate_rag` 本身不含重試，見步驟 6） |
| 生成提示詞、LLM 模型或溫度（`src/services.py`、`rag_pipeline.py`） | 要 |
| 路由規則（`query_router.py`） | 要，腳本會逐題檢查路由 |
| 知識庫同步、文件切分、`examples/` 手冊內容 | 要 |
| 前端、CI、Terraform、文件、快取 TTL、存取控制 | 不要 |

不需要就明講「這次變更不影響檢索品質，沒有評估」，不要為了看起來完整而跑。

### 2. 前置檢查

```bash
git status --short                       # 知道工作區有哪些未 commit 的變更
git log -1 --format=%H -- tests/rag_eval_report.json
```

- 需要三組金鑰：`OPENROUTER_API_KEY`、`OPENAI_API_KEY`、`PINECONE_API_KEY`。只檢查有沒有設定（不要印值）。缺了就停下請使用者處理。
- 確認目標 index：`PINECONE_INDEX_NAME` 必須是 demo index（預設 `rag-demo-qa-index`），不是正式 index。
- 告訴使用者：會花多少類型的費用（LLM 呼叫、embeddings、重排），會改動 demo index，並取得同意。

### 3. 取得比較基準

基準是**已 commit 的報告**：

```bash
git show HEAD:tests/rag_eval_report.json > /tmp/rag_eval_baseline.json
```

這個基準對應的是當初產生它的程式。如果你改的程式還沒跑過基準（報告比程式舊很多，或 `architecture.md` 評估段記錄的日期早於相關變更），要先在**沒有這次變更的版本**跑一次，不然比較的是「兩個變更的總和」。

### 4. 執行

腳本需要在專案 image 內，或裝齊依賴的環境執行；`tests/` 與 `examples/` 要在可讀路徑。它在單一程序內完成：同步範例手冊 → 檢查筆數 → 檢查檢索與路由 → 跑 RAGAS。

```bash
PINECONE_INDEX_NAME=rag-demo-qa-index \
DATA_SOURCE_DIR=/tmp/sample/uploaded_files BM25_INDEX_DIR=/tmp/sample/bm25 \
python -m scripts.eval_sample_manual --audit-index carbon-assistant-sample-index
```

`--audit-index` 填**目前的正式 index**：腳本在執行前後比對它的向量 ID，確認評估沒有動到正式資料；ID 變了就會失敗。需要時門檻可以覆寫：`RAG_EVAL_MIN_<METRIC>`（預設 0.7）。

這次執行會**覆寫 `tests/rag_eval_report.json`**（已被追蹤）。

### 5. 比較

```bash
python - <<'PY'
import json
old = json.load(open("/tmp/rag_eval_baseline.json"))["metrics"]
new = json.load(open("tests/rag_eval_report.json"))["metrics"]
for k in new:
    print(f"{k:20s} {old.get(k, float('nan')):.3f} -> {new[k]:.3f}  ({new[k]-old.get(k, 0):+.3f})")
PY
```

### 6. 解讀

- **雜訊**：RAGAS 用 LLM 當 judge，題庫只有 12 題，同一份程式重跑也會有小幅波動（沒有量過這個專案自己的變異幅度，所以保守以 0.05 為界）。**單次差距在 ±0.05 內不能說有變化**；要下結論先重跑一次。
- **等級**：任一指標低於門檻（腳本 exit code 1）是阻擋；沒過門檻但差距在雜訊內，先重跑；比基準退化超過 0.05 且重跑仍在，是重要問題。
- 報告只有四項彙總（`metrics`、`thresholds`、`passed`、`num_cases`），**沒有逐題分數**。要找出是哪一題變差，看腳本輸出的「標準答案來源是否出現在融合候選」題數與逐題路由檢查。
- **涵蓋範圍**：`evaluate_rag` 直接呼叫檢索與生成 chain，不經過路由、語意快取、自省重試與 guardrail，也沒有對話歷史，所以**不涵蓋低信心路徑**，也不能驗證重試是否有效。這些行為由單元測試與腳本的路由檢查負責。
- 題庫只有範例手冊的 12 題，結論限於這個題庫。

### 7. 報告檔與文件

跑完工作區的 `tests/rag_eval_report.json` 已被改寫。問使用者怎麼處理：

- **採用新結果**（變更是刻意的、數字可接受）：commit 新的報告，並同步 `architecture.md` 的評估段（日期、四項數字、題數、有沒有 audit index）與 `README.md` 引用的四個數字。三處要一致。
- **不採用**（只是驗證沒有退化、數字在雜訊內）：用 `git checkout -- tests/rag_eval_report.json` 還原，避免把雜訊當成變更。這會丟掉這次的輸出，所以先請使用者確認，需要的話先把結果貼在回報裡。

### 8. 回報

```
RAGAS 評估
- 變更：<改了什麼>
- 目標 index：<demo index>；audit：<正式 index> <未變動／已變動>
- 基準：<commit 或日期> → 這次：<日期>
| 指標 | 基準 | 這次 | 差 | 門檻 |
- 結論：通過／退化／在雜訊內（重跑 <n> 次）
- 沒涵蓋：路由與重試路徑、題庫僅 12 題
- 報告檔：已採用並同步文件／已還原
```

沒有實際執行的步驟就寫沒執行，不要用推測的數字。
