## Problem

混合檢索（hybrid search）的 BM25 那條腿對中文查詢完全失效：每次檢索的 `fusion_metadata.bm25_results` 中所有候選的 `score` 皆為 `0`。實測查詢「忘記密碼怎麼辦？」時，BM25 對全部文件給 0 分，融合排序實質上只由向量（dense）那條腿決定。BM25 沒報錯（`fusion_metadata` 無 `fallback` 鍵），是「靜默失效」，因此先前未被發現。結果是：號稱的 hybrid search 實際上幾乎是 vector-only，喪失了 BM25 對關鍵字/專有名詞精確匹配的價值。

## Root Cause

`src/bm25_index.py` 的 `_tokenize` 以 `text.split()`（按空白斷詞）切詞。中文字之間沒有空格，所以：

- query「忘記密碼怎麼辦？」被切成單一 token `["忘記密碼怎麼辦？"]`
- 文件「如果我忘記我的密碼，我該怎麼辦？」也被切成單一 token `["如果我忘記我的密碼，我該怎麼辦？"]`

兩者 token 交集為空集合（已實測驗證），`BM25Okapi.get_scores` 因無任何詞匹配而對每篇文件回傳 0。同樣的 `_tokenize` 在 build 與 search 都使用（共用設計正確），所以 corpus 也是以整句為單一 token 建索引，問題在兩端對稱發生。

## Proposed Solution

採業界標準作法：BM25 之前先做中文分詞。

- 將 `_tokenize` 從 `text.split()` 改為使用 **jieba** 的搜尋引擎模式 `cut_for_search`（在精確切詞基礎上再切細長詞，提升 recall，適合檢索場景）。
- 因 FAQ 為**繁體中文**，jieba 載入繁體大詞典 `dict.txt.big`（jieba 內建詞典偏簡體，對繁體斷詞品質較差）。
- 加上正規化：全形→半形、英文小寫化，並以 stopword 清單濾除高頻無資訊詞（的、我、怎麼辦…），提高有效關鍵字命中率。
- 新增 `jieba` 至專案依賴（`requirements.txt`），並讓 Dockerfile/image 能取得 `dict.txt.big`。
- build 與 search 維持共用同一 `_tokenize`（現已共用，無需改結構）。
- **重建索引**：現存持久化的 `tokenized_corpus`（GCS 上的 JSON）是用舊 `split` 切的整句 token。僅改 `_tokenize` 而不重建，query 用 jieba 切成多詞、corpus 仍是整句 → 仍 0 分。因此改完必須重跑 sync job 重建 BM25 index。

## Non-Goals

- **不導入學習式稀疏向量**（BGE-M3 sparse / SPLADE）取代詞彙式 BM25。那是更現代但工程量更大的升級路線，本change 只修好現有 BM25 的中文分詞。
- **不改 RRF 融合演算法或 `RRF_K`**：融合邏輯本身正確（用 rank 不用 score），修好分詞後排序自然改善。
- **不改向量檢索路徑**：dense 那條腿運作正常，不在本change 範圍。
- **不更換分詞器為 pkuseg/THULAC/HanLP**：jieba 對小型 FAQ 已足夠且最輕量，避免過度工程。

## Success Criteria

- 對中文查詢「忘記密碼怎麼辦？」，`fusion_metadata.bm25_results` 中與查詢共享詞（如「密碼」「忘記」）的文件 `score` > 0，且這類文件排名不落後於不含該詞的文件。
- 同一 `_tokenize` 在 build 與 search 兩端產生一致的中文詞級 token（例如「忘記密碼怎麼辦？」切出含「密碼」的多個 token，而非單一整句 token）。
- 重建索引後，融合結果（`fusion_results`）相對純向量基線有可觀察的排序變化，且品質複驗（「忘記密碼」「如何登入」等代表性問題）正解仍進入 Top 候選。
- 既有 BM25Index 的非分詞行為（doc_id 穩定性、JSON 持久化、版本指標檔、sync 重建、冷啟載入）不回歸。

## Impact

- Affected specs: `bm25-index`（新增「tokenizer 須做中文分詞」需求；既有 build/search/persist 需求行為不變）
- Affected code:
  - Modified:
    - `src/bm25_index.py`（`_tokenize` 改 jieba `cut_for_search` + 繁體詞典 + 正規化 + stopword）
    - `requirements.txt`（新增 `jieba`）
    - 容器映像建置設定（確保 runtime 取得 `dict.txt.big`，例如 Dockerfile 的相依/下載步驟）
  - New:
    - 可能新增 stopword 清單資源檔（若不以程式內常數表示）
  - Removed: （無）
- 部署/運維影響：改 `_tokenize` 與依賴需 rebuild image；上線後**必須觸發一次 sync job 重建 BM25 index**（舊 corpus token 不相容），否則 BM25 仍 0 分。走既有 CI（push `release/**`）部署，sync 由 UI 上傳或手動觸發。
