## 1. 重現問題的測試

- [x] 1.1 **重現證據以檢索 chain 的測試為準**（新參數尚不存在時，stage 層測試只會出現 TypeError，不算因 bug 失敗）。在 `tests/test_app_pipeline.py` 新增檢索 chain 測試，滿足 FAQ entries are reranked on question and answer：假 scorer 依文字給分（只有問題的那段給低分、問題＋答案那段給高分），斷言送出的配對含「問題＋空格＋答案」、沒有答案或答案為空白的候選只送問題、回傳 `rerank_scores` 的 top 分數取較高者；修正前會以斷言失敗。另在 `tests/test_rerank_stage.py` 新增新行為測試（不當重現證據），滿足 Rerank stage transforms top-M candidates into top-3 的「多段文字取最高分」範例：傳入給每個候選兩段文字的函式，假 scorer 依文字回傳範例表的分數，斷言回傳順序為 [B, A, C]、scorer 只被呼叫一次且收到 6 組配對；未傳函式、或函式給某候選空列表時只用 `page_content`；scorer 回傳分數數量與配對數不符時拋錯。並新增測試斷言 `2 * FUSION_TOP_M <= MAX_DOCUMENTS`，滿足 Rerank stage is resourced for bounded inference latency。驗證：修改程式前執行檢索 chain 測試，確認因只送出 `page_content` 而斷言失敗，記下失敗訊息。

## 2. 修正

- [x] 2.1 在 `src/rerank_stage.py` 的 `rerank_candidates` 新增可選參數（每個候選要評分的文字列表），一次呼叫 scorer、每個候選取最高分；`rerank_scores` 的 `content` 仍為 `page_content` 前 100 字。分數數量與配對數不符時拋錯（交給既有的 fallback）。在 `src/rag_pipeline.py` 定義產生「問題、問題＋空格＋答案」的函式（答案缺少、空字串或全空白時只回傳問題）並傳給 `rerank_candidates`。`tests/test_app_pipeline.py` 的 `patched_chain` fixture 原本固定回傳 4 個分數，改成依文字回傳分數，避免配對數變多後分數錯配。驗證：1.1 的測試與 `tests/test_rerank_stage.py`、`tests/test_app_pipeline.py` 其餘測試通過；`python -m pytest tests -q` 全部通過。
- [x] 2.2 改壞驗證：暫時讓檢索 chain 不傳入新函式（回到只用問題文字），確認 1.1 的檢索 chain 測試失敗後還原。驗證：記錄失敗的測試名稱與修正後的測試數量，寫進 fix commit 內文。

## 3. 評估與文件

- [x] 3.1 評估改在 `switch-reranker-to-vertex` 的任務 3.1 一併進行：2026-11-19 量測途中 Pinecone 重排額度用完，同一分支接著把重排器換成 Vertex AI Ranking，評估以最終上線的組合（Vertex＋取較高分）進行。已完成的修正前後離線量測（BGE，第一次檢索）：65 題手冊問題低信心 27 → 5 題，18 題手冊外問題誤判通過 2 → 2 題，重排失敗 0 次。驗證：`switch-reranker-to-vertex` 任務 3.1 完成，且本項的 BGE 數字寫進 `architecture.md` 的 Evaluation 段落。
- [x] 3.2 文件更新改在 `switch-reranker-to-vertex` 的任務 3.2 一併進行（第 5 節「重排」說明每個條目以問題與問題＋答案評分、取較高分；第 7 節改寫「top-1 分數這麼低，代表沒有任何 FAQ 問題相似」的說法；第 8 節註明 `top1_score` 從這次修改起與先前紀錄不能直接比較；`docs/sli-slo.md` 基準段落補註已處理）。驗證：`switch-reranker-to-vertex` 任務 3.2 完成且涵蓋上述各點。
