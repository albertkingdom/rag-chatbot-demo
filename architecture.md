# RAG_DEMO - RAG Architecture

## System Overview

```mermaid
flowchart TB
    subgraph UI["Frontend (React + TypeScript, frontend/dist)"]
        Chat[Chat 串流介面]
        Upload[手冊上傳 / Job 狀態]
    end

    subgraph Web["web (FastAPI, src/api/routes.py)"]
        Auth["/api/v1/auth/*"]
        ChatAPI["/api/v1/chat/stream<br/>NDJSON"]
        Admin["/api/v1/admin/manuals<br/>/api/v1/admin/jobs/{job_id}"]
        ChatSvc[chat_service<br/>chat_event_stream]
    end

    subgraph Infra["Infrastructure"]
        Redis[(Redis<br/>Semantic Cache · Chat History · RQ)]
        Worker[RQ Worker<br/>JOB_RUNNER=local]
        CRJob[Cloud Run Job<br/>JOB_RUNNER=gcp]
        Mongo[(MongoDB<br/>對話稽核紀錄)]
        BM25[(BM25 index<br/>bm25_data volume / GCS)]
        Reranker[BGE Reranker v2-m3<br/>web 程序內推論]
    end

    subgraph External["External Services"]
        OpenAI[OpenAI Embeddings]
        Pinecone[(Pinecone)]
        OpenRouter[OpenRouter<br/>Gemini 2.5 Flash]
        LangSmith[LangSmith]
    end

    Chat --> Auth
    Chat --> ChatAPI --> ChatSvc
    Upload --> Admin

    ChatSvc --> Redis
    ChatSvc --> Mongo
    ChatSvc --> BM25
    ChatSvc --> OpenAI
    ChatSvc --> Pinecone
    ChatSvc --> Reranker
    ChatSvc --> OpenRouter
    ChatSvc -.-> LangSmith

    Admin -- 入列 --> Redis
    Redis -- RQ job --> Worker
    Admin -- run_v2 --> CRJob
    Worker --> OpenAI
    Worker --> Pinecone
    Worker --> BM25
    CRJob --> OpenAI
    CRJob --> Pinecone
    CRJob --> BM25
```

部署：本機以 Docker Compose 啟動 `web`、`worker`、`redis`、`mongodb`、`mongo-express`、`redis-insight`；正式環境為單一 Cloud Run service（React SPA 與 API 同源），背景同步改由 Cloud Run Job 執行，`DATA_SOURCE_DIR` / `BM25_INDEX_DIR` 以 GCS 掛載共用，基礎設施由 `terraform/` 管理。

## Chat 請求流程（`src/chat_service.py`）

```mermaid
flowchart TD
    Q([使用者提問]) --> InGuard{輸入防護<br/>prompt injection}
    InGuard -- 命中 --> GuardMsg([安全訊息<br/>source=guardrail])
    InGuard -- 通過 --> Router{混合路由<br/>query_router}

    Router -- direct --> DirectGen[Gemini 直接生成]
    DirectGen --> OutGuard1{輸出防護<br/>PII / injection}
    OutGuard1 -- 命中 --> GuardMsg
    OutGuard1 -- 通過 --> SaveD[寫入 Redis 對話 · MongoDB] --> DirectAns([回覆 source=direct])

    Router -- rag --> Embed[rewritten_query 做 embedding]
    Embed --> Cache{語意快取<br/>cosine > 0.95}
    Cache -- hit --> OutGuard2{輸出防護}
    OutGuard2 -- 命中 --> GuardMsg
    OutGuard2 -- 通過 --> SaveC[寫入 Redis 對話 · MongoDB] --> CacheAns([回覆 source=cache])

    Cache -- miss --> Retrieve[Hybrid Retrieve<br/>BM25 top10 + Pinecone top10<br/>RRF k=60 → top5]
    Retrieve --> Rerank[BGE Rerank → top3]
    Rerank --> Grade{保留結果的<br/>rerank top-1 ≥ 0.3?}
    Grade -- 否, 未達重試上限且改寫查詢未重複 --> Retry[LLM 改寫查詢] --> Retrieve
    Grade -- 是 --> Gen[Gemini 生成<br/>context + 最近 3 輪]
    Grade -- 否, 無法再重試 --> LowGen[Gemini 生成<br/>低信心說明 · context 與來源皆為空] --> Prefix[答案開頭加固定說明]
    Gen --> OutGuard3{輸出防護}
    Prefix --> OutGuard3
    OutGuard3 -- 命中 --> GuardMsg
    OutGuard3 -- 通過, 高信心 --> Save[寫入語意快取<br/>＋ Redis 對話 · MongoDB]
    OutGuard3 -- 通過, 低信心 --> SaveLow[Redis 對話 · MongoDB<br/>不寫語意快取]
    Save --> RagAns([回覆 source=rag])
    SaveLow --> RagLow([回覆 source=rag<br/>不送來源])
```

所有路徑都會寫入 MongoDB（guardrail 擋下的也會，並附上命中的 PII 類型或 injection pattern）；direct、cache、rag 三條正常回覆路徑都會把這一輪寫入 Redis 短期對話；只有 rag 路徑會寫入語意快取，低信心的答案除外（見第 6、7 節）。rag 路徑寫入 MongoDB 時，metadata 帶有 `retrieval` 物件，記錄檢索分數與重試情形（見第 8 節）。

### 0. 輸入防護（`guardrails.py`）
以 regex 掃描 prompt injection（`INPUT_INJECTION_PATTERNS`，中英文）。規則用「動詞加目標」的句型，例如「ignore previous instructions」「reveal the system prompt」「reveal your API key」「繞過安全規則」，不用單字：`password`、`token`、`secret`、`api key`、`bypass` 單獨出現不會被擋，所以「How do I reset my password?」「What is a token?」可以通過。命中即回覆統一安全訊息，並以 `response_source="guardrail"` 寫入 MongoDB，不呼叫任何 LLM。

### 1. 混合路由與查詢改寫（`query_router.py`）
- **Stage 1 規則層（零延遲）**：先比對知識庫關鍵字 → `rag`。知識庫是 CarbonM 系統操作 Q&A，所以關鍵字包含碳盤查術語（碳、排放、燃料、冷媒、scope 1/2/3…）與系統功能（密碼、登入、帳號、邊界、審核、清冊、報告書…）；關鍵字最初依照先前的知識庫挑選；換成範例手冊後補上只在 CarbonM 情境出現的複合詞（盤查年度、盤查報告、範疇二、範疇三、第三方查證、係數庫），規則層可直接判定範例手冊 60 題中的 43 題（加上評估集共 52/72）。「係數」「盤查」「範疇」「廠區」「填報」「報表」「兩步驟驗證」等單詞不加入：子字串比對優先於 LLM，會讓「相關係數」「倉庫庫存盤查」「Google 表單填報截止」「iPhone 兩步驟驗證」這類問題直接走 rag。這些問題交給 LLM 層判斷，29 個一般問題反例列在 `scripts/eval_sample_manual.py` 的 `ROUTING_NEGATIVES`，規則層測試與評估腳本共用。代價是一般問題若含這些詞（例如「忘記 Gmail 密碼」）也會走 rag，此時 prompt 允許 context 不相關時改用一般知識回答。真實環境實測（2026-10-14）中，「忘記 Gmail 密碼」的 top-1 重排分數為 0.645，高於門檻，所以**不會**被視為低信心，而是以知識庫的重設密碼流程回答並附上來源；只有分數低於門檻的誤路由問題才會被視為低信心：開頭加上固定說明、不送來源、也不寫入語意快取（見第 7 節）。再比對一般問答模式（招呼、感謝、簡單算式、翻譯、寫程式、笑話）→ `direct`；其餘為 `uncertain`。領域關鍵字優先，避免同時符合兩者的查詢被誤判為 direct。
- **規則命中且有對話歷史**：只呼叫 LLM 做查詢改寫（`_rewrite_only`），把追問（如「4.2 呢？」）改寫成獨立問題。
- **Stage 2 LLM 層**：`uncertain` 的查詢交由 Gemini 2.5 Flash，以 Pydantic 結構化輸出（`RouterResult`）一次回傳 `route`、`rewritten_query`、`reasoning`。 prompt 列出知識庫涵蓋的功能類別（帳號與安全、角色與權限、廠區與盤查邊界、排放源與排放係數、數據填報匯入與審核、報表清冊與儀表板、第三方查證），並規定：碳盤查與排放的概念，或在系統中的操作且沒有指明其他產品 → `rag`；指明其他產品（Gmail、iPhone、Excel、Google 表單）、一般 IT／資安概念、其他領域的同名詞（統計的相關係數、倉庫的庫存盤查）、無關操作、常識與閒聊 → `direct`。2026-10-21 實測：範例手冊與評估集中交給 LLM 的 20 題全部判為 `rag`（先前「如何修改個人資料？」「如何開啟兩步驟驗證？」會被判為 `direct`），28 個交給 LLM 的反例全部判為 `direct`，連續兩次結果相同。
- **錯誤處理**：LLM 路由失敗時回退為 `direct`（不查知識庫）；`_rewrite_only` 失敗時沿用原始問題。
- 規則命中 `direct` 且有對話歷史時，仍會呼叫一次 `_rewrite_only`，但 direct 路徑生成時使用原始問題，該改寫結果不會被用到。
- 這層取代了舊的 intent classifier（舊版只能擋離題問題，不會分流到直接生成）。

### 2. Direct 路徑
不檢索、不查語意快取，直接以 QA prompt 生成（context 為空），生成後同樣經過輸出防護。

### 3. 語意快取（`cache_service.py`）
以改寫後的查詢做 OpenAI embedding，在 Python 中與快取索引內**全部**條目計算 cosine similarity，取相似度最高且 **> 0.95** 的一筆視為命中（TTL 24 小時）。命中的答案仍須通過 PII / injection 檢查才回傳；寫入時以同一個 embedding 為 key。

- **命名空間**：快取依 Pinecone index 分開。資料 key 為 `prompt_cache:<PINECONE_INDEX_NAME>:<hash>`，索引為 `prompt_cache:<PINECONE_INDEX_NAME>:index:zset`；讀不同知識庫的 revision 或部署不會讀到彼此的答案，切換 index 時新命名空間從空開始。`PromptCacheService(namespace="")` 使用不含命名空間的舊格式（只供測試）；先前版本寫入的無命名空間 key 在 24 小時內自然過期。
- **索引**：每個命名空間一個 Redis Sorted Set，成員是資料 key，score 是寫入時間（秒）。上限為 `CACHE_MAX_ENTRIES`（預設 50）：每次寫入在同一個交易中依序登記、選出最新 N 筆以外的成員、從索引移除，交易完成後再刪除這些成員的資料 key。淘汰是先進先出，命中不會更新順序；同一問題重複寫入只更新 score 並保留 `hit_count`。
- **查詢**：取索引最近 N 筆（也就是整個索引），以單一 `MGET` 讀回；索引為空時不發出讀取。已過期的成員在同一次查詢中移出索引；內容無法使用的成員（不是 JSON 物件、`answer` 不是非空字串、`sources` 缺漏或不是字串清單）也一併移出；embedding 缺漏、不是數字陣列或維度不符的只略過。單筆損壞不會讓整次查詢失敗。
- **舊索引**：先前版本的 Set 索引 `prompt_cache:index` 不遷移，查詢與寫入都不再讀寫它；部署後快取等同冷啟動，舊資料 key 在 24 小時內自然過期。`clear_cache` 刪除目前命名空間的索引與舊 Set 索引，以及兩者登記的資料 key；Redis 出錯時拋出例外，不會回傳 0 假裝成功。
- **同步後清除**：手冊同步在向量與 BM25 都成功後清除目前命名空間的快取（見 Document Ingestion）。
- **成本**：每次走 rag 的請求都要讀回並解析整個索引（50 筆約 1.8MB），而且是在 async 路徑中同步執行（見[已知問題](#已知問題)）。`scripts/benchmark_cache_lookup.py` 以合成資料量測：50 筆約 17 毫秒、100 筆約 35 毫秒（開發機，不含網路傳輸），耗時約九成在 JSON 解析。

### 4. 混合檢索（`hybrid_retriever.py`）
- **BM25**：in-process `rank_bm25`，取前 `BM25_TOP_N`（10）筆。查詢前最多每 `BM25_RELOAD_INTERVAL_SECONDS`（30 秒）檢查一次 pointer 檔（`reload_if_stale()`），指向新版本時就重新載入，因此同步完成後 web 不需重啟，最多約 30 秒內改用新索引。pointer 檔只在手冊同步完成時更新，正式環境又放在 GCS 掛載目錄，所以不每次查詢都讀。索引不存在、為空或出錯時，自動降級為 `vector_only`。
- **向量檢索**：Pinecone，以非同步 `asimilarity_search_with_score` 取前 `VECTOR_TOP_N`（10）筆（不支援時才 fallback 為 `asyncio.to_thread`）。呼叫以 lock 序列化。
- **執行順序**：BM25（同步、in-process）先跑完，再 await 向量檢索，兩者**不是並行**。
- **RRF 融合**：以 Reciprocal Rank Fusion（`RRF_K=60`）合併兩路排名，取前 `FUSION_TOP_M`（5）筆（由 10 減半以縮短重排時間）。
- BM25 與向量結果、融合分數、fallback 原因記錄於 `fusion_metadata`，供 LangSmith 追蹤。

### 5. 重排（`rerank_stage.py`、`rag_pipeline.py`）
`BAAI/bge-reranker-v2-m3` cross-encoder 在 web 程序內對 query–doc 成對評分，取 Top 3（`RERANK_TOP_K`）。`rag_pipeline.py` 以 `asyncio.to_thread` 呼叫，避免阻塞 event loop。

### 6. 自省式檢索（`retrieval_grader.py`）
以 reranker top-1 分數判斷檢索品質，低於 `GRADE_SCORE_THRESHOLD`（0.3）時，由 LLM 參考低品質結果改寫查詢並重跑檢索與重排，最多 `RETRIEVAL_MAX_RETRIES`（1）次。直接沿用重排分數，不額外呼叫 LLM 評分。

- **保留較佳結果**：重試結果只有在 top-1 分數嚴格較高時才取代原結果，分數相同或較差都保留較早的結果；沒有檢索結果視為最低分。是否通過評分，以最終保留的結果判斷。
- **停止重複重試**：改寫後的查詢（去除前後空白）為空，或與任何已試過的查詢相同（包含改寫失敗時回傳原查詢），就不再檢索並結束重試。改寫用的那一次 LLM 呼叫仍然會發生，省下的是檢索與重排。
- **重試次數**：紀錄的 `retry_count` 只計實際執行的檢索，被略過的重試不計。
- 重試迴圈與保留邏輯位於 `chat_service.py`；`retrieval_grader.py` 只提供評分（`grade_documents`）、取得 top-1 分數（`get_top_score`）與改寫（`rewrite_for_retry`）。

### 7. 生成（`rag_pipeline.py`）
Gemini 2.5 Flash（OpenRouter，temperature 0）。Prompt 包含今天日期（台灣時區）、最近 3 輪對話（`format_history` 預設 `max_turns=3`）、Top 3 context，並要求以繁體中文回答；context 不相關時可改用一般知識回答。檢索使用改寫後的查詢，生成時則以原始問題搭配對話歷史。

**低信心**：最終保留的檢索結果未通過評分（top-1 低於門檻，或完全沒有結果；等於門檻視為通過）時，視為低信心。此時仍會生成，但：

- prompt 的 system 訊息加入低信心說明（`LOW_CONFIDENCE_NOTE`）：只有資料明確寫出答案時才可引用；關於 CarbonM 產品本身（功能、版本、價格、是否支援某項目等）而資料沒寫的問題，要說明無法確認，不推測也不斷言有或沒有；只有一般常識性的問題才以一般知識簡答；不得杜撰具體操作步驟、畫面或選單名稱。低信心時**不把檢索到的 context 交給模型**（context 以空字串傳入）：top-1 分數這麼低，代表沒有任何 FAQ 問題與使用者的問題相似，內容只會讓模型去描述無關的段落（真實環境曾出現「這段文字主要說明了如何進入…儀表板」）。模型只靠一般知識與提示詞回答。
- 生成完成後、輸出防護之前，伺服器在答案開頭固定加上 `LOW_CONFIDENCE_PREFIX` 與一個空行，所以標示不依賴模型是否遵守；串流文字、Redis 對話與 MongoDB 紀錄是同一份文字。
- 回答為空時不加固定說明。
- 不送出來源事件，也不寫入語意快取；回應來源仍是 `rag`。
- 若答案被輸出防護攔截，沿用既有的防護行為（回覆安全訊息、不寫對話歷史與快取）。
- 固定說明與低信心說明都以測試確認不會觸發輸入輸出防護規則。

### 8. 輸出防護與回應（`guardrails.py`、`chat_events.py`）
完整答案生成後，先做 PII（email、電話、信用卡、身分證字號、IP）與 injection 掃描，通過後才以 typed NDJSON 事件分段送出。偵測規則：

- **email**：取 `@` 前後最長的位址字元連續段，不用 `\b`，所以緊貼中文的 email 也會被偵測；網域必須在 `GUARDRAIL_ALLOWED_EMAIL_DOMAINS`（預設 `example.com`，設為空字串則全擋）內才放行，網域需完全相等，子網域不自動放行，多個 `@` 的連續段一律攔截。
- **電話**：只比對台灣手機、台灣市話與 `+` 開頭的國際格式，不會誤判日期或一般數字。
- **信用卡**：13–19 位數字（位數間至多一個空白或連字號）且通過 Luhn 檢查，每個以分隔處結尾的候選都會檢查（「4111 1111 1111 1111 123」仍會命中）；每組都是 1900–2100 四位數的年份串（如「2023 2024 2025 2026」，Luhn 剛好通過）不算。
- **IP**：四段數字且每段 ≤255。
- **injection（輸出端）**：`OUTPUT_INJECTION_PATTERNS` 只判斷答案正在呈現系統或開發者指令的句型（例如「My system prompt: …」「Here is my system prompt」「系統提示：…」），單純提到 system prompt、API key、token 的答案可以通過。這是盡力偵測，不保證攔下改寫過的洩漏。

輸入端的 `INPUT_INJECTION_PATTERNS` 以「動詞＋目標」判斷，並刻意偏向攔截針對助理本身的請求：指令覆寫（ignore/disregard/forget/override 後同一子句內出現 your、previous、system 等字與 instructions/rules/prompts，否定提醒如「do not forget your rules」除外；忽略/忘記/無視 加上 你的、之前的、所有 等與 指令/規則/指示，但說話者自己忘記的「我忘記之前的規則了」除外）、提示擷取（列出/提供/分享 等動詞加上「你的系統提示」，不論後面接什麼；沒有「你的」時只在目標位於句尾時攔截，讓「請列出系統提示的錯誤碼」這類詢問產品通知的問題通過；以及「把系統提示…貼出來」句型與英文 reveal/print the system prompt、bare `system prompt`）、索取金鑰密碼、繞過安全規則。「What is a token?」「How do I reset my password?」「我忘記密碼了」「登入後系統提示密碼錯誤」等一般問題不會命中。

輸入端與輸出端用不同清單，`detect_prompt_injection(text, direction=...)` 由呼叫端指定；命中後的處理方式不變。

**防護定位**：這個專案的輸入端規則刻意只擋明顯的攻擊句型，優先避免誤擋正常問題。理由是模型沒有工具或寫入權限（唯讀 RAG）、系統 prompt 就在公開 repo、知識庫是虛構的範例手冊，injection 成功的影響僅限於回答內容；主要防線是 prompt 本身。因此不另外以 LLM 判斷輸入。

| 事件 | 內容 |
|------|------|
| `status` | 只在 rag 路徑送出：`retrieving`（embedding 與查快取之前）、`reranking`（混合檢索、重排與重試之前）、`generating`（生成之前）。direct 路徑不送 status |
| `delta` | 答案分段文字 |
| `sources` | 檢索來源摘要；低信心的答案不送出（視為沒有來源） |
| `metadata` | `elapsedMs`、`responseSource`（rag / direct / cache / guardrail）、`cacheHit` |
| `done` / `error` | 結束或錯誤 |

rag 路徑的 MongoDB 紀錄（包含被輸出防護攔截的）在 `metadata.retrieval` 記錄：`top1_score`（保留結果的 top-1 分數，無結果為 null）、`attempt_scores`（每次實際檢索的 top-1 分數，原查詢在前）、`retry_count`、`kept_attempt`（0 為原查詢）、`low_confidence`、`threshold`（評分當下的門檻）與 `retry_skipped`（是否因改寫查詢為空或重複而提早結束重試）。快取命中與 direct 路徑沒有檢索，不帶此欄位。

Redis 短期對話以 `CHAT_HISTORY_TTL_SECONDS` 控制（預設閒置 30 分鐘過期），保留 `CHAT_HISTORY_MAX_TURNS` 輪。

## Sequence Diagram（RAG 路徑）

```mermaid
sequenceDiagram
    participant U as React UI
    participant F as FastAPI
    participant GR as Guardrail
    participant RT as Router
    participant G as Gemini
    participant O as OpenAI Embeddings
    participant R as Redis
    participant B as BM25
    participant P as Pinecone
    participant RR as BGE Reranker
    participant M as MongoDB

    U->>F: POST /api/v1/chat/stream
    F->>GR: Input injection scan
    alt Injection detected
        F-->>U: guardrail message (NDJSON)
    else Passed
        F->>RT: route_query(message, history)
        RT->>RT: Rule stage
        opt uncertain, or rule hit with history
            RT->>G: route + rewrite / rewrite only
        end
        RT-->>F: route, rewritten_query
        F-->>U: status: retrieving
        F->>O: embed(rewritten_query)
        F->>R: semantic cache lookup
        alt Cache hit
            F->>GR: PII / injection scan
            F->>R: append chat history
            F->>M: save conversation
            F-->>U: cached answer
        else Cache miss
            F-->>U: status: reranking
            F->>B: BM25 top 10
            F->>P: vector top 10 (after BM25)
            F->>F: RRF fuse → top 5
            F->>RR: rerank → top 3
            opt top-1 score < 0.3 (max 1 retry; keep the better attempt, stop on a repeated query)
                F->>G: rewrite for retry
                F->>B: re-retrieve
                F->>P: re-retrieve
                F->>RR: re-rerank
            end
            F-->>U: status: generating
            Note over F,G: low confidence: add low-confidence note to the prompt, prepend fixed notice to the answer
            F->>G: generate (context + history)
            G-->>F: full answer
            F->>GR: PII / injection scan
            F->>R: store cache (skipped when low confidence) + chat history
            F->>M: save conversation
            F-->>U: delta… sources (omitted when low confidence), metadata, done
        end
    end
```

## Document Ingestion（`build_vector_store.py`、`manual_service.py`）

```mermaid
flowchart TB
    Up["POST /api/v1/admin/manuals<br/>PDF / XLSX / CSV ≤ 25 MB"] --> Save[存入 DATA_SOURCE_DIR]
    Save --> Enqueue{JOB_RUNNER}
    Enqueue -- local --> RQ[RQ Worker]
    Enqueue -- gcp --> Job[Cloud Run Job]
    RQ --> Sync
    Job --> Sync

    subgraph Sync["sync_vector_store()"]
        Backfill[CSV/XLSX 回填 uuid 欄位<br/>寫回來源檔]
        Extract["抽取 Q&A pair"]
        Dedup["去除完全相同的 Q&A"]
        Diff{以 doc_id 比對<br/>Pinecone 既有 ID}
        EmbedB[OpenAI Embedding<br/>100 筆/批]
        Upsert[Upsert 100 筆/批]
        Del[刪除本地已不存在的 ID]
        Rebuild[重建 BM25 index]
        Backfill --> Extract --> Dedup --> Diff
        Diff -- 新 ID --> EmbedB --> Upsert
        Diff -- 已移除 ID --> Del
        Upsert --> Rebuild
        Del --> Rebuild
        Diff -- 無變動也執行 --> Rebuild
    end

    Poll["GET /api/v1/admin/jobs/{job_id}"] -.-> Enqueue
```

- `doc_id` 取自來源檔的 `uuid` 欄位；CSV/XLSX 缺 uuid 時會產生並寫回原檔，寫回失敗則中止同步，避免以不穩定的 ID upsert。PDF 沒有 uuid 欄位，每次同步都會在記憶體中產生新的 ID。
- 差異比對**只看 ID**：新的 ID 會 upsert，已不在本地的 ID 會刪除。若同一個 uuid 的問答內容被修改，不會被重新 embedding。
- **PDF 每次同步都會全量重做**：PDF 的 ID 每次重新產生，所以既有 PDF 向量全部被刪除、再重新 embedding 與 upsert。
- 若 Pinecone `list()` 失敗，視為索引為空：所有文件重新 upsert，且不刪除任何向量。
- 無論有無差異，每次同步都會用完整的本地文件重建 BM25 index；重建失敗不影響已完成的向量同步。
- 向量同步與 BM25 重建都成功後，清除目前命名空間的語意快取並印出筆數；BM25 失敗時不清除，清除失敗（含 import 或 Redis 錯誤）只記錄警告，同步結果仍為成功。
- 以 `python -m src.build_vector_store` 執行（Cloud Run Job）時，同步回傳 `status=error` 或拋出例外都以結束碼 1 結束；本機 RQ worker 執行的 `run_sync()` 在同一情況下拋出例外。兩種 runner 的介面輪詢都會顯示失敗。
- 清除快取時直接以 `REDIS_URL` 建立 Redis 連線，不載入聊天用的 `services`。
- web 載入 BM25 時印出 `[BM25Index] Loaded persisted index (<N> docs) from <path>`（立即 flush），切換正式站時以這行確認新 revision 已載入新索引。
- web 端最多每 30 秒檢查一次 pointer 檔，同步完成後約 30 秒內的查詢就會載入新索引。

### Pinecone index 與範例手冊

- index 名稱來自 `PINECONE_INDEX_NAME`，未設定或空字串時為 demo index `rag-demo-qa-index`（首次同步時自動建立）。聊天、同步 Job 與 `scripts/` 下操作 Pinecone 的腳本（重複向量分析與刪除、結構檢查）都讀同一個設定；腳本會先載入 `.env` 再讀設定。
- 正式環境由 `terraform/main.tf` 的 locals 在 web Service 與 sync Job 設定 `carbon-assistant-sample-index`，資料目錄為 GCS 上的 `/mnt/data/sample/uploaded_files` 與 `/mnt/data/sample/models`。先前的 `carbon-assistant-qa-index`（他方手冊，126 筆）與 `/mnt/data/uploaded_files`、`/mnt/data/models` 保留供回復，尚未刪除。2026-10-21 起（v1.1.0）正式站已切換到範例手冊。
- 同步會刪除 index 中不在本地手冊裡的向量，所以不同部署或環境不可共用同一個 index。
- `examples/carbonm_sample_manual.xlsx` 是自行撰寫的虛構範例手冊（60 筆，五類主題），可直接在「文件管理」上傳；附固定的 `uuid` 欄位，重新同步時 doc_id 不變。
- `scripts/eval_sample_manual.py` 在單一程序中把範例手冊同步到 demo index，檢查 demo index 與 BM25 筆數等於手冊筆數、逐題確認 hybrid 檢索沒有 fallback 且有 BM25 結果（並列出標準答案來源出現在融合候選中的題數，未命中不中止），再以實際路由檢查範例手冊與評估集全部問題必須為 `rag`、`ROUTING_NEGATIVES` 必須不為 `rag`（LLM 路由錯誤重試一次並分開回報），最後執行 RAGAS。遇到正式 index 名稱（`carbon-assistant-sample-index`、`carbon-assistant-qa-index`）會直接拒絕；預設不讀其他 index，加上 `--audit-index <正式 index>` 時才會在執行前後比對該 index 的向量 ID（中途失敗或拋例外也會比對）。三組 API key 缺任一組時在呼叫 Pinecone 前就結束。需在專案 image 中執行，並另外掛入 `tests/`、`scripts/`、`examples/`（image 不含 `tests/`）。
- **既有本機環境遷移**：預設 index 改變後，舊的本機 Compose 會改用空的 demo index，但 `manual_data`、`bm25_data` volume 與 Redis 語意快取仍保留舊資料。要沿用舊資料，在 `.env` 設定原本的 `PINECONE_INDEX_NAME`；要改用範例手冊，因介面沒有刪除手冊的功能，需以 `docker compose down -v` 清空本機資料（含已上傳手冊、BM25 索引、Redis 快取與 MongoDB 紀錄）後重新啟動，再上傳範例手冊並等待同步。
- **正式站切換與回復**：步驟見 [docs/deployment/releases.md](docs/deployment/releases.md#切換正式站知識庫)。web Service 與 sync Job 必須一起改 index 與資料目錄；切換和回復期間不可上傳手冊，因為 web 觸發的 Job 使用 Job 自己的設定。

## Key Parameters

| Component | Parameter | Value | 定義位置 |
|-----------|-----------|-------|----------|
| Vector Store | `PINECONE_INDEX_NAME` | `rag-demo-qa-index`（正式環境為 `carbon-assistant-sample-index`） | `config.py`（env 可覆寫；正式值在 `terraform/main.tf`） |
| Vector Retrieval | `VECTOR_TOP_N` | 10 | `config.py` |
| BM25 Retrieval | `BM25_TOP_N` | 10 | `config.py` |
| Fusion | `RRF_K` | 60 | `config.py` |
| Fusion | `FUSION_TOP_M` | 5 | `config.py` |
| Reranking | `RERANK_TOP_K` | 3 | `rerank_stage.py` |
| BM25 Reload | `BM25_RELOAD_INTERVAL_SECONDS` | 30 | `config.py`（env 可覆寫） |
| Retrieval Grader | `GRADE_SCORE_THRESHOLD` | 0.3 | `config.py`（env 可覆寫） |
| Retrieval Grader | `RETRIEVAL_MAX_RETRIES` | 1 | `config.py`（env 可覆寫） |
| Semantic Cache | `CACHE_SIMILARITY_THRESHOLD` / `CACHE_TTL_SECONDS` | 0.95（嚴格大於）/ 24 hours | `config.py` |
| Semantic Cache | `CACHE_MAX_ENTRIES` | 50（索引上限，同時是查詢比對的範圍；須為不小於 1 的整數，未設定或無效時為 50，無效值會記錄警告） | `config.py`（env 可覆寫；正式環境的 docker-compose.yml 與 Terraform 尚未傳遞這個變數，目前無法在正式環境調整） |
| Chat History | `CHAT_HISTORY_MAX_TURNS` / `CHAT_HISTORY_TTL_SECONDS` | 3 / 30 minutes | `config.py`（env 可覆寫） |
| Prompt History | `max_turns` | 3 | `rag_pipeline.py`（`format_history` 預設值） |
| Embedding | Dimensions / batch | 1536 / 100 docs per call | `build_vector_store.py` |
| Vector Upsert | Batch Size | 100 vectors | `build_vector_store.py` |
| LLM | Model / temperature | `google/gemini-2.5-flash` / 0 | `services.py` |
| Access | `RATE_LIMIT_RPM` | 60 | `config.py`（env 可覆寫） |

## Evaluation

- `tests/generate_rag_eval_data.py`：從知識庫抽樣 Q&A，由 LLM 把問題改寫成口語問法，原答案作為 `ground_truth`，輸出到 draft 檔，經人工審核後才升為 `tests/rag_eval_data.json`。
- `tests/evaluate_rag.py`：用 golden set 直接呼叫檢索 chain（hybrid + rerank）與生成 chain，不經過路由、語意快取、自省重試與 guardrail，也不帶對話歷史，因此也不涵蓋低信心路徑（固定說明、省略來源、不寫快取）；以 RAGAS 評估 faithfulness、answer_relevancy、context_precision、context_recall，judge 沿用專案的 Gemini 與 OpenAI embeddings。報告寫入 `tests/rag_eval_report.json`；任一指標低於門檻（預設 0.7，可用 `RAG_EVAL_MIN_<METRIC>` 覆寫）時 exit code 為 1。
- `tests/evaluate_rerank.py`：評估重排效果。
- ragas（0.2.x 到 0.4.3）在 import 時需要 langchain-community 0.4 已移除的 Vertex AI 類別，而 langchain-community 不能降回 0.3（會讓 reranker 無法 import）。`tests/ragas_compat.py` 在 import ragas 前補上不會被使用的替代類別，`tests/test_ragas_compat.py` 確保 ragas 與 reranker 都能 import。
- 題庫 12 題，`ground_truth` 逐字取自 `examples/carbonm_sample_manual.xlsx`，五類主題（帳號、盤查邊界、排放源、數據審核、報表）各至少 2 題；`tests/test_sample_manual.py` 檢查每題答案都在範例手冊中。
- `tests/rag_eval_report.json` 於 2026-10-21 以 `scripts/eval_sample_manual.py --audit-index carbon-assistant-qa-index` 在 demo index 上產生：faithfulness 1.0、answer_relevancy 0.95、context_precision 0.94、context_recall 1.0，全部高於 0.7 門檻，12 題的標準答案來源都出現在融合候選中，正式 index 的向量 ID 前後一致。同一天較早的執行（手冊尚未改寫兩個與原手冊相同的問句時）為 1.0、0.93、0.86、0.92，當時「有好幾個廠區，要怎麼調整今年要納入盤查的範圍？」找不到來源；手冊問句改為「多廠區要怎麼切換盤查邊界？」後與口語問法有共同字詞而命中。知識庫只對問題文字建索引，口語問法與手冊問句用詞差異大時仍可能漏檢。評分帶有 LLM 隨機性，比較前後版本時應多跑幾次取平均。

## Performance Optimizations

| 優化 | 位置 | 效果 |
|------|------|------|
| Embedding 改批次（100 筆/次） | `build_vector_store.py` | API 呼叫數約減少 100 倍，同步時間明顯縮短（見下方估計） |
| Rerank 改 `asyncio.to_thread` | `rag_pipeline.py` | cross-encoder 推論不再阻塞 event loop |
| Fusion 候選 10 → 5 | `config.py` | 重排推論量減半 |
| 規則路由 | `query_router.py` | 明確查詢不需呼叫 LLM |
| 語意快取 | `cache_service.py` | 相似問題跳過整條 RAG |
| 答案分段 yield | `chat_service.py` | 以固定大小分段串流 NDJSON `delta` |

批次 embedding 前後的同步時間**粗略估計**（未保留量測腳本與環境紀錄，僅供參考）：

| Documents | Before | After | Speedup |
|-----------|--------|-------|---------|
| 50 | ~20s | ~3s | 6x |
| 200 | ~90s | ~8s | 11x |
| 1000 | ~7min | ~25s | 16x |

## 已知問題

原本列在這裡的問題已修正；目前仍存在的問題都列在下一節。

### 2026-10-14 實測發現的問題（尚未處理）

來源：真實環境端到端測試（OpenAI、OpenRouter、Pinecone 唯讀、本機 BGE 重排器、隔離的 Redis 與 MongoDB）與程式碼審查。以下問題都還沒有建立變更提案。

| 問題 | 位置 | 影響 |
|------|------|------|
| 章節號「1.2.3.4」會被判成 IP，輸出端 injection 與輸入端的少見措辭仍有誤判或漏判 | `guardrails.py`：`ip_address`、`OUTPUT_INJECTION_PATTERNS`、`INPUT_INJECTION_PATTERNS` | 四段數字的章節號形狀上是合法 IP，答案帶有時整段被攔；「print the API key name」這類措辭會被輸入端攔下；輸出端只攔標題加冒號與「here is my system prompt」等句型，改寫過的洩漏攔不到 |
| 輸入端偏向攔截，提到「你的規則／指令」的正常問題會被拒答 | `guardrails.py`：`INPUT_INJECTION_PATTERNS` | 字串比對無法分辨「使用者忘了」與「要求模型忘掉」，因此選擇攔截為主：例如「請忘記之前的規則設定」「Forget your validation rules and …」一律攔截；更精準的判斷需改用 LLM 分類（每次多一次呼叫） |
| 輸入端字串規則有誤擋也有漏擋 | `guardrails.py`：`INPUT_INJECTION_PATTERNS` | 相對舊版單字比對，「Send me the API key」「Copy the access token」「繞過內容審核」等改寫後的索取或繞過句型不再被擋（為了不誤擋「What is a token?」「How do I reset my password?」）；描述第三方的句子如「棋手忽略先前的規則」「Why do CSS declarations override earlier rules」會被擋。依上方的防護定位接受此取捨 |
| 上傳的手冊內容可能帶有間接 injection | `manual_service.py`、`rag_pipeline.py` | 所有登入者都能上傳手冊，內容會進入生成 prompt；模型沒有工具，影響限於回答內容 |
| 用雙空格或「 - 」分隔的卡號不會被偵測 | `guardrails.py`：`credit_card` | 規則只允許數字間一個空白或連字號，「4111  1111  1111  1111」會通過；放寬時需保留 Luhn、年份串排除與分組切分 |
| 關鍵字誤路由的問題不會被低信心機制攔下 | `query_router.py` 規則層 | 「忘記 Gmail 密碼」的檢索分數 0.645，高於門檻，仍以重設密碼流程回答並附來源 |
| 快取查詢與寫入以同步 Redis 呼叫在 async 路徑執行 | `chat_service.py` 呼叫 `cache_service.py` | 查詢期間阻塞同一個事件迴圈；快取查詢範圍擴大後，每次讀回的資料量更大 |
| 同一 index 更新手冊後仍可能寫入舊快取 | `build_vector_store.py` 同步後清快取、`hybrid_retriever.py` 每 30 秒檢查 BM25 pointer | 清除之後、web 載入新 BM25 之前（最長約 90 秒，含 GCS FUSE stat cache），或同步前已開始的請求，仍可能寫入依舊資料產生的答案並保留 24 小時；可用資料版本作為命名空間的一部分解決 |
| 命中計數以 get、ttl、setex 三步更新，不是原子操作 | `cache_service.py`：`_increment_hit_count` | 與同一 key 的重新寫入或淘汰交錯時，可能以舊內容覆蓋新值，或在淘汰後留下一筆沒有索引的資料 key（最長 24 小時後過期）；不會造成錯誤命中，窗口極小 |
| 評分門檻 0.3 缺乏標註資料驗證 | `config.py`：`GRADE_SCORE_THRESHOLD` | 實測樣本中知識庫內問題 0.89–1.0、知識庫外約 0.0001，區分明顯，但樣本很小；接近門檻的問題行為未知 |
| 約三成知識庫問題仍需 LLM 路由 | `query_router.py`：`RAG_KEYWORDS` | 範例手冊 60 題有 17 題由 LLM 判斷，每題多約 1–2.6 秒；評估腳本的路由關卡確保它們判為 `rag`，但 LLM 判斷可能隨模型或 prompt 改變而退化，修改後需重跑評估腳本 |
| 正式 index 名稱寫在兩處 | `terraform/main.tf`（`local.pinecone_index_name`）、`scripts/eval_sample_manual.py`（`PRODUCTION_INDEXES`） | 之後再改正式 index 時，評估腳本的拒絕清單要一起更新；執行時仍應以 `--audit-index` 明確指定要保護的 index |
| 評估題庫只有 12 題 | `tests/rag_eval_data.json` | 題數仍少，LLM 評分的隨機性足以影響版本比較；也沒有涵蓋路由、重試與低信心路徑 |
