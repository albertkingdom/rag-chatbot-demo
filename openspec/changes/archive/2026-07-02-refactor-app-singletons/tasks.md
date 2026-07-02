## 1. services.py — thread-safe singleton providers

- [x] 1.1 建立 `src/services.py`,把 `src/app.py` 的 8 個 singleton(`_reranker`/`_embeddings`/`_llm`/`_vectorstore`/`_retrieval_chain`/`_generation_chain`/`_bm25_index`/`_hybrid_retriever`)+ `_intent_classifier` + `_get_optimal_device` + `get_*` factory 全數搬入。每個 singleton 用 per-instance `threading.Lock` + double-checked locking(`if _x is None: with _lock: if _x is None: _x = build()`)。provider 缺 key 時拋 `ValueError`(保留原訊息)。驗收:`grep -c "threading.Lock" src/services.py` ≥ 9;`import src.services` 不觸發任何建構;`from src.services import get_embeddings, get_llm, get_vectorstore, get_bm25_index, get_hybrid_retriever, get_retrieval_chain, get_generation_chain, get_reranker_model, get_intent_classifier` 全部可匯入(實作 Thread-safe singleton providers;對應設計決策「Thread-safe provider 模式」)。
- [x] 1.2 撰寫 `tests/test_services.py` 驗證 thread-safe:(a)兩 thread 並發呼叫 `get_reranker_model()`(以 mock `build` 函式 + `time.sleep` 模擬慢建構)拿到同一實例(`id()` 相等);(b)`get_llm()` 第二次呼叫返回同一實例不重入 lock 區;(c)`get_embeddings()` 在 `OPENAI_API_KEY` 未設時拋 `ValueError`。hermetic(mock 外部服務)。驗收:`pytest tests/test_services.py -v` 全綠(實作 Thread-safe singleton providers 的並發情境)。

## 2. rag_pipeline.py — RAG 業務邏輯

- [x] 2.1 建立 `src/rag_pipeline.py`,把 `_QA_PROMPT`、`_format_docs`(`@traceable`)、`_parse_history_turns`、`format_history`、`rewrite_query`(`@traceable`)、`chat_stream` 從 `app.py` 搬入。從 `src.services` 取 provider,從 `src.config` 取常數,從 `src.access_control`/`src.conversation_db`/`src.guardrails`/`src.hybrid_retriever`/`src.rerank_stage` 取既有元件。`chat_stream` 的 `gr.Request` 參數與 yield 行為保持不變。驗收:`from src.rag_pipeline import chat_stream, rewrite_query, format_history` 可匯入;`src/app.py` 不再定義這些符號(實作 Layered module separation 的 business logic 層;對應設計決策「模組分層與職責」)。

## 3. ui.py — Gradio UI 與 handler

- [x] 3.1 建立 `src/ui.py`,把 `with gr.Blocks(...)` UI 定義、`bom_mapper_func`、`upload_manual_func`、`poll_status` 從 `app.py` 搬入。`demo` 變數在此定義(`ui.demo`),供 `app.py` mount。handler 從 `src.services` 取 provider、從 `src.rag_pipeline` 取 `chat_stream`、從 `src.bom_mapper` 取 `classify_bom_headers`、從 `src.build_vector_store` 取 `sync_vector_store`。驗收:`from src.ui import demo` 可匯入且為 `gr.Blocks` 實例;`src/app.py` 不含 `gr.Blocks`/`gr.Tab`/handler 定義(實作 Layered module separation 的 UI 層)。

## 4. bom_mapper.py — 移除 module-level 副作用

- [x] 4.1 移除 `src/bom_mapper.py` 的 `API_KEY = os.getenv("OPENROUTER_API_KEY")` 與 module-level `llm = ChatOpenAI(...)`。`llm_batch_classify_headers`/`llm_batch_verify_headers`/`classify_bom_headers` 改為函式內 `from .services import get_llm; llm = get_llm()`(若 `OPENROUTER_API_KEY` 未設,`get_llm` 拋 `ValueError`,與現行 `app.py` 一致)。驗收:`grep -E "^API_KEY = |^llm = " src/bom_mapper.py` 無結果;`import src.bom_mapper` 在未設 `OPENROUTER_API_KEY` 時不拋例外(實作 No module-level side effects on import;對應設計決策「bom_mapper 副作用消除」)。

## 5. app.py — 縮減為入口 + re-export

- [x] 5.1 改寫 `src/app.py` 為入口:`from .access_control import mount_auth`、`from .ui import demo`、`app = FastAPI()`、`mount_auth(app)`、`gr.mount_gradio_app(app, demo, path="/")`、`__main__`。末尾加顯式 re-export `from .services import (get_embeddings, get_llm, get_vectorstore, get_bm25_index, get_hybrid_retriever, get_retrieval_chain, get_generation_chain, get_reranker_model, get_intent_classifier)`。驗收:`wc -l src/app.py` < 50;`grep -E "^_(reranker|embeddings|llm|vectorstore|retrieval_chain|generation_chain|bm25_index|hybrid_retriever|intent_classifier)\s*=\s*None" src/app.py` 無結果;`from src.app import get_embeddings` 仍可匯入(實作 Layered module separation 的 app 層 + 向後相容 re-export)。

## 6. 測試調整

- [x] 6.1 調整 `tests/test_app_pipeline.py` 的 `monkeypatch.setattr` 目標:原 `monkeypatch.setattr(app_module, "get_bm25_index", ...)` 等改為 patch `src.services`(因 `rag_pipeline` 內呼叫的 `get_*` 來自 `services`)。確保 `test_chain_returns_five_keys`、`test_context_built_from_answer_metadata`、`test_top3_docs_order`、`test_fusion_metadata_present` 通過。驗收:`pytest tests/test_app_pipeline.py -v` 全綠(實作 Existing tests remain green against new structure;對應設計決策「測試 patch 目標調整」)。
- [x] 6.2 修正 `tests/test_rag_stream.py` 的 `from app import rag_chain`(已過時,`rag_chain` 不存在)→ 改為 `from src.rag_pipeline import chat_stream` 並調整測試邏輯(呼叫 `chat_stream` 而非 `rag_chain.astream`);或若該腳本已無對應業務,標記 skip 並附理由。驗收:`pytest tests/test_rag_stream.py -v` 不再 `ImportError`(實作 Existing tests remain green against new structure 的 test_rag_stream 情境)。
- [x] 6.3 檢查 `tests/test_bom_mapper.py` 是否依賴 `bom_mapper` 的 module-level `llm`;若是,改為 mock `src.services.get_llm` 或在測試內設 `OPENROUTER_API_KEY`。確保 `pytest tests/test_bom_mapper.py -v` 全綠。驗收:全綠(實作 No module-level side effects on import 的測試面)。

## 7. 全套驗證

- [x] 7.1 跑 `venv/bin/python -m pytest tests/ -v` 全套(含 access_control、health、services、app_pipeline、rag_stream、bom_mapper、bm25_index、cache_service、hybrid_retriever、intent_classifier、rerank_stage 等),確認重構未破壞任何既有測試。驗收:`pytest tests/ -v` 全綠;無 `ImportError`/`AttributeError`/patch 目標錯誤。

## 8. Review fix — Redis 連線收斂與 lazy 統一(Redis 連線收斂與 lazy 統一)

- [x] 8.1 在 `src/config.py` 新增 `REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")` 常數(集中管理,取代散落三處的 `os.getenv`;實作 Single Redis connection source 的 REDIS_URL centralization 部分)。驗收:`from src.config import REDIS_URL` 可匯入且預設值正確(手動 content review + 匯入指令)。
- [x] 8.2 在 `src/services.py` 新增 `get_redis_conn()` provider:用 `LazySingleton`(見 task 8.6)或手寫 double-checked locking + `threading.Lock`,`build` 呼叫 `redis.from_url(REDIS_URL)`。驗收:`from src.services import get_redis_conn` 可匯入;`import src.services` 不建連線;兩次呼叫 `get_redis_conn()` 回同一實例(`id()` 相等)(實作 Redis 連線收斂與 lazy 統一)。
- [x] 8.3 改 `src/ui.py`:移除 module-level `_conn = redis.from_url(...)` 與 module-level `q = Queue(...)`。`q` 改成 lazy 取得(在 `upload_manual_func` 與 `poll_status` 內透過 `get_redis_conn()` + `Queue(connection=...)` 建立,或用一個 `_get_queue()` helper)。`poll_status` 內 `Job.fetch(..., connection=_conn)` 改用 `get_redis_conn()`。驗收:`grep -n "redis.from_url\|^_conn = \|^q = Queue" src/ui.py` 無結果;`import src.ui` 在無 Redis 時不建連線(手動驗證:未啟動 Redis 下 `venv/bin/python -c "import src.ui"` 不拋且不觸發連線)。
- [x] 8.4 改 `src/rag_pipeline.py`:移除 `_get_redis_conn()` 函式與 `_conn` 變數;`chat_stream` 內兩處 `PromptCacheService(_get_redis_conn())` 改為 `PromptCacheService(get_redis_conn())`(從 `src.services` import)。驗收:`grep -n "_get_redis_conn\|_conn" src/rag_pipeline.py` 無結果;`from src.rag_pipeline import chat_stream` 仍可匯入。
- [x] 8.5 改 `src/access_control.py`:`build_auth_config` 內 `redis.from_url(redis_url)` 改為 `from .services import get_redis_conn; ... get_redis_conn()`(沿用集中來源)。`REDIS_URL` env 讀取改用 `config.REDIS_URL`。`AuthConfig.redis` 欄位行為不變(仍回傳連線實例)。驗收:`grep -n "redis.from_url" src/access_control.py` 無結果;`venv/bin/python -m pytest tests/test_access_control.py -v` 全綠(認證/限流/session 行為不變)。
- [x] 8.6 撰寫 `tests/test_redis_conn.py`(或併入 `tests/test_services.py`):驗證 `get_redis_conn()` 兩次呼叫回同一實例(mock `redis.from_url` 回傳 MagicMock);`import src.ui` 不觸發 `redis.from_url`(spy);`get_redis_conn` 在並發下只建一次。hermetic。驗收:`pytest tests/test_redis_conn.py -v`(或 `test_services.py::TestRedisConn`)全綠。

## 9. Review fix — rag_pipeline 兩 chain 補註解(rag_pipeline 兩個 chain 不上鎖的取捨)

- [x] 9.1 在 `src/rag_pipeline.py` 的 `get_retrieval_chain` 與 `get_generation_chain` 各加一行(或多行)註解,說明「刻意不上鎖:建構僅包裝已被 services.py 鎖保護的 provider,冪等且微秒級成本;race 最壞結果是多建一個等價 pipe 物件,無副作用、無重複昂貴 I/O」。驗收:content review 確認兩函式各含此說明;`pytest tests/test_app_pipeline.py -v` 仍全綠(行為不變)。

## 10. Review fix — 死 import 清理(死 import 清理)

- [x] 10.1 刪除 `src/rag_pipeline.py` 未引用的 `FUSION_TOP_M`(從 `from .config import (...)` 行移除)。驗收:`grep -n "FUSION_TOP_M" src/rag_pipeline.py` 無結果;`venv/bin/python -c "import src.rag_pipeline"` 成功。
- [x] 10.2 刪除 `src/bom_mapper.py` 已無用的 `import os` 與 `from langchain_openai import ChatOpenAI`(module-level LLM 移除後這兩者不再被引用)。驗收:`grep -nE "^import os|^from langchain_openai import ChatOpenAI" src/bom_mapper.py` 無結果;`venv/bin/python -c "import src.bom_mapper"` 成功;`pytest tests/test_bom_mapper.py -v` 無 ImportError。

## 11. Review fix — LazySingleton helper 收斂(LazySingleton helper 收斂)

- [x] 11.1 在 `src/services.py` 新增 `LazySingleton` class:`__init__(self, build)` 存 build callable + `threading.Lock` + `_value=None`;`get(self)` 內含 double-checked locking(`if _value is None: with lock: if _value is None: _value = build()`)。把 `get_embeddings`/`get_llm`/`get_vectorstore`/`get_hybrid_retriever`/`get_reranker_model`/`get_redis_conn` 6 個純 build-once provider 改用 `LazySingleton` 實例(module-level 建 `_embeddings = LazySingleton(lambda: ...)` 等);`get_bm25_index`(有 `reload_if_stale`)與 `get_intent_classifier`(缺 key 回 None 不快取)維持手寫。驗收:`grep -c "threading.Lock" src/services.py` ≤ 3(2 個手寫 provider + 1 個 helper 內);`pytest tests/test_services.py -v` 全綠(含並發測試);`import src.services` 不觸發建構。

## 12. Review fix — 全套驗證

- [x] 12.1 跑 `venv/bin/python -m pytest tests/ -v` 全套,確認所有 review fix 未破壞既有測試,且新測試(redis_conn)通過。驗收:`pytest tests/ -v` 結果與 review fix 前相比無新增失敗(2 個 pre-existing cache_service 失敗可接受);新 `test_redis_conn` 全綠。
