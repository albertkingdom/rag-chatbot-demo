## 1. 依賴與詞典

- [x] 1.1 在 `requirements.txt` 新增 `jieba`（不釘特定版本則與專案既有風格一致；釘版本則記錄於 commit）。
- [x] 1.2 確保 runtime 取得繁體大詞典 `dict.txt.big`：在容器映像建置設定（Dockerfile）中取得該檔（隨 image 打包或建置時下載到固定路徑），並以環境變數或常數記錄其路徑供 `_tokenize` 載入。
- [x] 1.3 在 `src/bm25_index.py` 模組載入時呼叫一次 `jieba.set_dictionary(<dict.txt.big 路徑>)`（只設定一次，避免每次 tokenize 重載詞典）。

## 2. 改寫分詞器（src/bm25_index.py）

- [x] 2.1 新增正規化 helper：全形→半形、Latin 字元小寫化（套用於 `_tokenize` 輸入文字），使「ＡＢＣ／abc」等表面變體一致。
- [x] 2.2 新增 stopword 過濾：以一份繁中常見停用詞清單（程式內常數或資源檔，至少涵蓋「的、了、我、你、嗎、怎麼辦」等高頻無資訊詞）濾除 token。
- [x] 2.3 將 `_tokenize` 由 `text.split()` 改為：先正規化 → `jieba.cut_for_search`（搜尋引擎模式）→ 去除空白/標點 token → 套用 stopword 過濾，回傳詞級 token list。維持 `@staticmethod` 介面不變，使 build 與 search 仍共用同一份分詞邏輯。
- [x] 2.4 驗證 `_tokenize("忘記密碼怎麼辦？")` 產出包含「密碼」「忘記」等詞的多 token 清單（非單一整句 token），且與文件「如果我忘記我的密碼，我該怎麼辦？」的 token 有非空交集。此即 spec 需求「Tokenizer performs Chinese word segmentation」要求的中文詞級分詞 + build/search 共用同一 `_tokenize`。

## 3. 測試

- [x] 3.1 新增單元測試：以兩三篇中文 Q&A Document build `BM25Index`，對共享詞的中文 query 呼叫 `search`，斷言匹配文件 `bm25_score > 0` 且排名不落後於無共享詞的文件（對應 spec scenario「Chinese query produces non-zero scores」）。
- [x] 3.2 新增單元測試：對同一段文字分別經 build 路徑與 search 路徑分詞，斷言兩者 token list 完全相同（對應 spec scenario「same tokenizer at build and search」）。
- [x] 3.3 跑既有 BM25Index 相關測試，確認 doc_id 穩定性、JSON 持久化、版本指標檔、冷啟載入等未回歸。

## 4. 部署與重建索引

- [x] 4.1 commit（feature/fix-bm25-chinese-tokenization）→ merge 進 master → merge 進 `release/gcp-deployment` push，觸發既有 CI（amd64 build + push + scoped terraform apply），帶上新 `_tokenize`、`jieba` 依賴與 `dict.txt.big`。
- [ ] 4.2 上線後**觸發一次 sync job 重建 BM25 index**（UI 上傳知識庫檔或手動觸發 job），使 GCS 上的 `tokenized_corpus` 以新 jieba 分詞重建。確認 sync `exit(0)`、log 顯示 vectors/BM25 重建完成。

## 5. 品質複驗

- [ ] 5.1 在 UI 送「忘記密碼怎麼辦？」並取得 `_retrieve` 的 `fusion_metadata`，確認 `bm25_results` 的 score **不再全為 0**、共享詞文件有正分數（對應 Success Criteria）。
- [ ] 5.2 抽查 3–5 題代表性中文問題（如「如何登入」「忘記密碼」「如何登出」），確認融合後 Top 候選含正解、且相對純向量基線排序有合理改善；記錄任何品質退化以決定是否調整 stopword 或詞典。
