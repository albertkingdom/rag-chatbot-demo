# RAG 學習筆記

> 以本專案（碳管理 FAQ 助手）為實例，整理 RAG 的核心觀念與設計取捨。
> 本專案技術棧：FastAPI + Gradio + Pinecone + Redis + MongoDB + OpenRouter(Gemini 2.5 Flash) + BGE Reranker。

---

## 目錄

1. [Agentic RAG vs 固定流程 RAG](#1-agentic-rag-vs-固定流程-rag)
2. [兩階段檢索：向量檢索 + Reranker](#2-兩階段檢索向量檢索--reranker)
3. [Reranker 的分數：算什麼、什麼尺度](#3-reranker-的分數算什麼什麼尺度)
4. [Guardrail：RAG 的防護機制](#4-guardrailrag-的防護機制)
5. [關鍵字檢索與 Hybrid Search](#5-關鍵字檢索與-hybrid-search)
6. [向量 DB 的儲存模型：向量與 metadata 綁定](#6-向量-db-的儲存模型向量與-metadata-綁定)
7. [圖片在 RAG 裡怎麼處理](#7-圖片在-rag-裡怎麼處理)
8. [前處理（Ingestion）才是品質天花板](#8-前處理ingestion才是品質天花板)

---

## 1. Agentic RAG vs 固定流程 RAG

**核心差異：誰來決定流程怎麼走。**

| | 固定流程 RAG（本專案） | Agentic RAG |
|---|---|---|
| 誰決定流程 | 工程師寫死 | LLM 執行時動態決定 |
| 檢索次數 | 固定一次 | 0 到多次，看需要 |
| 能否換工具 | 不行，路徑固定 | 可路由到不同工具/資料源 |
| 能否自我修正 | 不行 | 可以（檢索差就重試） |
| 延遲/成本 | 低、可預測 | 高、不固定 |
| 可預測性 | 高（好除錯、好保證 guardrail） | 低 |
| 適合場景 | 單一來源、查表式問答 | 多來源、多跳推理 |

**比喻**：固定流程像工廠輸送帶（每件走同樣工序，快又穩）；Agentic 像請研究員（自己決定查幾次、查哪裡，聰明但慢又貴）。

**本專案的判斷**：知識庫是單一來源 FAQ，且整個專案在優化延遲/成本，**不適合整個改成 Agentic**。真正值得局部導入 agentic 的只有兩點：
- **Corrective RAG**：檢索品質差時觸發重試，而非永遠硬塞低相關 context。
- **工具路由**：FAQ 問答 vs BOM Mapper 之間自動選擇。

落地建議：用 LangGraph 把線性流程改成 graph，**只在檢索後加一個條件分支**，保留快取/防護的確定性路徑。

---

## 2. 兩階段檢索：向量檢索 + Reranker

RAG 常用「先粗篩、再精選」的兩階段設計：

| 階段 | 模型類型 | 算分方式 | 特性 |
|------|---------|---------|------|
| **向量檢索 (Recall, k=10)** | Bi-encoder（OpenAI Embedding + Pinecone） | query 與文件**各自**轉向量，再算 cosine | 快、可預建索引，但精度粗 |
| **重排 (Rerank, Top 3)** | Cross-encoder（BGE Reranker v2-m3） | query 與文件**一起**進模型做注意力交互 | 慢、無法預建索引，但精度高 |

**為什麼要兩階段**：知識庫可能上千筆，不可能全部交給昂貴的 Cross-encoder。先用便宜的向量檢索把範圍縮到 10 筆，再用精準的 reranker 細選出 3 筆。

**Reranker 的角色**：它**不負責找候選**（那是 Pinecone 的工作），它負責**把找出來的候選重新評分、精選**——這就是為什麼叫 *re*ranker（重排器）而非 retriever。

### Bi-encoder vs Cross-encoder
- **Bi-encoder**：分別編碼 query 和 doc → 兩個向量算距離。可預計算 doc 向量，快但無法捕捉 query-doc 交互。
- **Cross-encoder**：query 和 doc 拼接一起輸入 → 直接輸出一個相關性分數。精準但每次都要即時算，無法預建索引。

---

## 3. Reranker 的分數：算什麼、什麼尺度

### 算的對象
```python
scores = model.score([(query, doc.page_content) for doc in docs])
```
10 筆候選進去 → 10 個分數出來，一一對應。分數 = **(query, 文件) 這一對的相關性**，越高越相關。

⚠️ **本專案重要細節**：`doc.page_content` 存的是 **FAQ 的「問題」**，答案放在 `metadata["answer"]`。所以：
- 向量檢索、重排比對的都是 **使用者 query ↔ 知識庫的「問題」**（question-to-question matching）。
- **答案從頭到尾沒參與檢索或評分**，只在生成階段被當 context 引用。
- 後果：若某筆 FAQ 的問題寫得跟答案落差很大，使用者用「答案裡的關鍵字」提問時可能檢索不到。

### 分數的尺度（最容易踩雷的地方）
- Cross-encoder 原生輸出是 **raw logits（任意實數，可正可負）**，數值本身沒有機率意義，只有相對大小有意義。
- 要變成 0~1 需額外套 **sigmoid**，是否自動套取決於實作（`HuggingFaceCrossEncoder` 預設不一定套）。

兩個實務結論：
1. **拿來排序、取 Top N → 沒問題**（排序只看相對大小）。
2. **拿來設絕對門檻 → 要先實測分數分佈**，不能憑感覺設值（見第 4 節 grounding 翻車案例）。

### 尺度不通用
| | 數值來源 | 範圍 | 能混用門檻嗎 |
|---|---|---|---|
| Pinecone cosine | 向量夾角 | -1 ~ 1 | — |
| Reranker score | Cross-encoder logits | 任意實數（除非 sigmoid） | ❌ 與 cosine 不同尺度 |

---

## 4. Guardrail：RAG 的防護機制

### 業界 RAG guardrail 防的三道關卡

**輸入端（防被操控/濫用）**
- Prompt Injection / 越獄
- 離題 / 範圍濫用
- 惡意 / 毒性輸入

**檢索/生成端（RAG 最核心、純聊天 bot 沒有的）**
- **幻覺 / 答案不接地（Faithfulness）**：檢索沒提到卻自己編
- **沒有相關資料卻硬答**：該說「不知道」卻瞎掰
- **存取控制 / 資料越權**（企業 RAG 第一大坑）：檢索到使用者無權看的文件
- 過期 / 矛盾資料

**輸出端（防洩漏/不當內容）**
- 個資 / 機密外洩
- 系統提示洩漏
- 毒性 / 偏見 / 合規問題

**橫切面**：速率限制、Agentic 的無限迴圈。

> 重點：純聊天 bot 的 guardrail 重心在「輸入防攻擊、輸出防毒性」；**RAG 的重心是中間那層——防幻覺、防資料越權**，因為 RAG 的賣點就是「有根據、可信」。

### 本專案的覆蓋狀況
| 關卡 | 狀況 | 實作 |
|------|------|------|
| 輸入 Prompt Injection | ✅ | regex（`guardrails.py`） |
| 離題過濾 | ✅ | 意圖分類（Gemini） |
| 輸出 PII | ✅ | regex |
| 輸出注入 | ✅ | regex |
| **防幻覺（grounding）** | ❌ **已被移除** | — |

### 規則式 guardrail 的限制
- **注入偵測靠關鍵字**：換句話說、同義詞、夾空白、換語言就能繞過。
- **PII regex 有誤判風險**：本專案 `credit_card` 規則 `(?:\d[ -]*?){13,19}` 幾乎把任何 13~19 位數字當卡號；`ip_address` 會把版本號誤判。可能誤擋合法答案。

### 防幻覺檢查被移除的故事（重要案例）
1. **原始設計**：`check_context_similarity` 用 OpenAI Embedding + Cosine、閾值 0.72，比對「回答 vs context」——防幻覺。
2. **問題**：使用者問英文、知識庫是中文時，跨語言 embedding 相似度偏低，**正確答案也被擋（false positive）**。
3. **嘗試修**：改用 BGE Reranker（跨語言較好），閾值降到 0.3。但 **0.3 是憑空抓的、沒校準**（文件自己寫「上線後再統計分佈決定」）。
4. **最後直接移除**（commit `d1f47c7`）：理由「簡化邏輯、減少 overhead」，同時刪掉面試指南的「Case B 誤判」段落——顯示誤殺問題仍未解決。
5. **語意飄移**：被刪前，程式實際傳的是 `rewritten_query`（不是回答）比對 `context_questions`（FAQ 問題），已從「防幻覺」漂移成「檢索相關性把關」，簽章 `response` 卻傳 query。

**教訓**：若要重新接回 grounding 檢查——
- 明確定義要防什麼（防幻覺就老實比對「回答 vs context 答案」）。
- **先從歷史對話撈分數、看分佈再定閾值**，不要憑感覺設 0.3。

---

## 5. 關鍵字檢索與 Hybrid Search

**本專案目前是純語義向量檢索（dense retrieval），沒有關鍵字檢索。**

### 純向量檢索的盲點
| 情況 | 純向量的問題 | 關鍵字(BM25)的優勢 |
|------|------------|-------------------|
| 精確代號/編號（類別 4.2、料號） | 4.1 / 4.2 容易混 | 字面精準匹配 |
| 專有名詞/縮寫 | 罕見詞向量表示弱 | 直接命中字串 |
| 數字/版本 | 「100」和「1000」語義相近 | 精確區分 |

本專案知識庫有「類別 4.1/4.2」「門檻值」這類帶編號內容，正是純向量易錯處。

### Hybrid Search（混合檢索）
dense（管意思）+ sparse/BM25（管字面），分數融合（常用 RRF）。兩條路：
1. **Pinecone 原生 hybrid**：要改 `dotproduct` metric + 產生 sparse 向量，**需重建索引**。
2. **LangChain `EnsembleRetriever`**：把 `BM25Retriever` 和 Pinecone retriever 加權融合，**不動 Pinecone，改動小**，適合先 A/B 驗證。

⚠️ 別為加而加：先撈「檢索錯誤」案例確認問題真出在「字面沒匹配」，再決定。

---

## 6. 向量 DB 的儲存模型：向量與 metadata 綁定

向量 DB 每筆記錄是三件事綁在一起：
```
{
  "id":       "qa_12345",
  "values":   [0.013, -0.21, ...],   ← 向量（被搜尋的對象）
  "metadata": { "answer": ..., "text": ... }   ← 附帶資料，跟著回傳
}
```

**關鍵觀念：被搜尋的只有向量，metadata 是「搭便車」回傳的。**

| | 角色 | 會被語義檢索嗎 |
|---|---|---|
| `values`（向量） | 由 page_content 算出 | ✅ 被比對相似度 |
| `metadata`（如 answer） | 附帶資料 | ❌ 不被搜尋，只跟著回傳 |

這就是為什麼本專案「答案放 metadata」= 答案被儲存回傳，但**永遠不參與相似度計算**。

**metadata 的第二個用途：過濾（filter）**。可加 `category`、`source` 等欄位，檢索時下條件只在特定範圍找。
> Pinecone 限制：可過濾型別僅 string / number / bool / list[str]，每筆 metadata 上限約 40KB。

---

## 7. 圖片在 RAG 裡怎麼處理

核心思路：**先把圖裡的資訊轉成「可檢索的形態」**。四種策略：

| 策略 | 做法 | 適合 | 複雜度 |
|------|------|------|--------|
| ① 圖轉文字描述（Captioning） | vision LLM 產生描述，當文本索引 | 流程圖、示意圖、照片 | 低（不改架構） |
| ② OCR 抽文字 | 抽出圖中文字 | UI 截圖、掃描頁、表格 | 低-中 |
| ③ 多模態向量（CLIP 類） | 圖文 embedding 到同空間 | 以圖找圖 | 高 |
| ④ 生成時餵原圖 | metadata 存圖片路徑，回答時把原圖給多模態 LLM | 需「看圖才能解釋」 | 中 |

**對本專案（操作手冊 = UI 截圖 + 流程圖）的建議**：**策略② + ①**
```
建索引時每張圖：
  ① OCR 抽出截圖文字（按鈕、欄位、路徑）
  ② vision LLM 產生「這張圖在示範 XXX」的描述
  ③ ①②合併成文字 + 圖片路徑存 metadata
     → 進入現有 embedding/Pinecone/rerank 流程，不改架構
```
進階再加策略④：回答時附上原圖。

⚠️ **最常踩的坑：圖文分家**。解析時別讓圖片和它的上下文被切到不同 chunk，否則檢索到「這張圖顯示一個按鈕」卻不知道是哪個功能。讓圖片描述跟所在段落/章節綁在一起。

---

## 8. 前處理（Ingestion）才是品質天花板

**業界共識：RAG 的品質大半在前處理就決定了，不是檢索或生成。** 「garbage in, garbage out」在 RAG 特別明顯。這步最不性感卻最決定成敗。

### 為什麼難
| 難點 | 兩難 |
|------|------|
| 切割粒度 | 太大→主題混雜稀釋 embedding；太小→上下文斷裂 |
| 語意邊界 | 固定字數切割會把完整概念硬切兩半 |
| metadata 萃取 | 分類/來源/章節常要再花 LLM 或人工標 |
| 格式雜訊 | PDF 頁首頁尾、表格、多欄排版解析常亂 |

### 本專案「聰明避開」了複雜度
知識庫是 **Q&A pair**，每筆「問題+答案」就是**天然自足的 chunk**——不用煩惱切幾個字、語意邊界，metadata 也單純。**這對 FAQ 是正確設計，不是偷懶**。複雜度只在「離開 Q&A 格式、直接解析圖文混排手冊」時才回來。

### 若要走到自動解析，怎麼控制複雜度
1. **用自然邊界切**（章節/標題/段落/Q&A），別用固定字數。
2. **讓 LLM 一次做完「結構化 + metadata」**：整頁丟給 LLM 輸出結構化 chunk + 章節 + 摘要 + 圖片描述，用 LLM 換工程複雜度。
3. **別追求一次完美**：先簡單上線，從 LangSmith/MongoDB 撈「檢索失敗」案例，回頭針對性修 ingestion。前處理是迭代出來的。

---

## 附：本專案值得補強的優先序（綜合以上）

1. **防幻覺（grounding）**：用數據校準閾值後重新接回 `check_context_similarity`（見第 4 節）。
2. **PII regex 收緊**：卡號、IP 兩條規則過鬆，會誤擋合法答案。
3. **（視需要）Hybrid Search**：若實測「編號類問題」檢索常錯，用 `EnsembleRetriever` 加 BM25。
4. **（視需要）局部 Agentic**：Corrective RAG 重試 + FAQ/BOM 工具路由，用 LangGraph 漸進改造。

> 共同原則：先用數據（LangSmith / MongoDB 歷史對話）確認問題真實存在，再動手，別憑感覺加功能——這與本專案「優化延遲/成本」的定位一致。
