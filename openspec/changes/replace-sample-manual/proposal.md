## Why

目前知識庫與評估資料來自他方提供的操作手冊，repo 已公開，這些內容（尤其 `tests/rag_eval_data.json` 的標準答案）不宜繼續隨程式碼散布；同時 repo 沒有附任何可上傳的手冊，他人照 README 啟動後無法建立知識庫。

## What Changes

- 新增自行撰寫的 CarbonM 虛構操作手冊 `examples/carbonm_sample_manual.xlsx`（約 60 筆 question/answer，主題涵蓋帳號與登入、盤查邊界與多廠區、排放源與排放係數、數據條目與審核、報表與清冊），格式可直接由「文件管理」上傳同步。
- `PINECONE_INDEX_NAME` 改為可由同名環境變數覆寫，預設值改為 demo index `rag-demo-qa-index`；正式環境由 Terraform 在 web Service 與 sync Job 明確設定 `carbon-assistant-qa-index`，行為不變。
- 新手冊只同步到 `rag-demo-qa-index`，同步與評估在一次性容器內執行，不寫入正式 index、不使用既有 volume。
- 兩支刪除重複向量的腳本改為先載入 `.env` 再讀取設定。
- `tests/rag_eval_data.json` 改以新手冊為依據並擴充為 12 題；在新 index 上重跑 RAGAS，更新 `tests/rag_eval_report.json` 與 `architecture.md` 的評估數據與已知問題。
- 前端建議問題、路由與 BM25 測試、smoke 腳本中引用舊手冊的問句，改為新手冊中存在的問句。
- README 快速開始改為「上傳 `examples/` 的範例手冊」。

## Non-Goals

- 不更名產品（保留 CarbonM），不調整路由關鍵字清單的語意。
- 不切換正式站的 Pinecone index，也不修改正式站知識庫；是否切換另行決定。
- 不清理 git 歷史中的舊評估資料。
- 不改變評估腳本、生成器或檢索流程的行為。

## Capabilities

### New Capabilities

- `sample-knowledge-base`: repo 隨附自製範例手冊，Pinecone index 名稱可由環境變數覆寫，評估資料以範例手冊為依據。

### Modified Capabilities

(none)

## Impact

- Affected specs: `sample-knowledge-base`（新增）
- Affected code:
  - New: examples/carbonm_sample_manual.xlsx, tests/test_sample_manual.py, tests/test_config.py, scripts/eval_sample_manual.py
  - Modified: src/config.py, .env.example, docker-compose.yml, terraform/main.tf, scripts/remove_duplicates_auto.py, scripts/detect_and_remove_duplicates.py, tests/test_document_identity.py, tests/rag_eval_data.json, tests/rag_eval_report.json, frontend/src/App.tsx, tests/test_query_router.py, tests/test_bm25_index.py, scripts/real_rag_smoke.py, scripts/understand_embeddings.py, architecture.md, README.md
