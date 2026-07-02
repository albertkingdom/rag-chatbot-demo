## Summary

將 637 行的 `src/app.py` God Module 拆分為分層模組,並把 8 個全域可變 singleton 改為 thread-safe 的集中式 provider,消除並發 race condition 與 module-level 副作用。

## Motivation

`src/app.py` 目前把以下全部塞進一個檔:
- 8 個 singleton(`_reranker`、`_embeddings`、`_llm`、`_vectorstore`、`_retrieval_chain`、`_generation_chain`、`_bm25_index`、`_hybrid_retriever`)+ 各自的 `get_*()` factory;
- `_get_optimal_device`、`_QA_PROMPT`、`_format_docs`、`format_history`、`rewrite_query`、`chat_stream`(RAG 業務);
- `bom_mapper_func`、`upload_manual_func`(BOM/上傳 handler);
- Gradio `with gr.Blocks` UI 定義;
- FastAPI `app = FastAPI()` + `gr.mount_gradio_app`。

問題:
1. **God Module**(637 行):難以導航、測試要 `monkeypatch.setattr` 私有欄位、改動易衝突。
2. **非 thread-safe singleton**:8 個 `global _x; if _x is None: _x = build()` 在並發下有 race condition(uvicorn 多請求、Gradio 內部 thread pool)。`get_reranker_model()` 載入 BGE 模型(~2s)期間,第二個請求可能看到 `None` 並重複載入。
3. **module-level 副作用**:`src/bom_mapper.py:16` 在 `import` 時就建構 LLM(`llm = ChatOpenAI(...)`),導致任何 `import bom_mapper` 都會嘗試連 OpenRouter,測試無法隔離。

業界標準:分層(api/service/data)、DI 容器或 lazy thread-safe provider、避免 module import 副作用。

## Proposed Solution

1. **拆分 `app.py` 為分層模組**:
   - `src/services.py`(新):集中所有 singleton provider(`get_embeddings`、`get_llm`、`get_vectorstore`、`get_bm25_index`、`get_hybrid_retriever`、`get_retrieval_chain`、`get_generation_chain`、`get_reranker_model`、`get_intent_classifier`),每個用 `threading.Lock` + double-checked locking 保證 thread-safe 且只初始化一次。
   - `src/rag_pipeline.py`(新):`_QA_PROMPT`、`_format_docs`、`format_history`、`rewrite_query`、`chat_stream`(RAG 業務邏輯,從 services 取 provider)。
   - `src/bom_mapper.py`(改):移除 module-level `llm = ChatOpenAI(...)`,改為函式內 lazy 取得(透過 services 的 `get_llm`)。
   - `src/ui.py`(新):Gradio `gr.Blocks` UI 定義 + `bom_mapper_func` + `upload_manual_func` handler。
   - `src/app.py`(改,大幅縮減):只剩 `app = FastAPI()`、`mount_auth(app)`、`gr.mount_gradio_app(app, ui.demo, path="/")`、`__main__`。
2. **thread-safe provider**:在 `services.py` 每個 provider 用 `lock = threading.Lock()` + `global _x; if _x is None: with lock: if _x is None: _x = build()`(double-checked locking)。
3. **bom_mapper 副作用消除**:`bom_mapper.py` 的 `classify_bom_headers` 在內部呼叫 `get_llm()` 取得 LLM,而非 module import 時建構。
4. **保留 `src/app.py` 對外公開符號**:若任何測試或腳本 `from src.app import get_xxx`,在 `app.py` 保留 re-export(`from .services import get_embeddings, ...`)以向後相容,避免破壞既有 import。
5. **更新測試**:`test_app_pipeline.py`、`test_rag_stream.py` 等若 `monkeypatch.setattr(app_module, "get_xxx", ...)` 改為 patch `src.services` 或透過 provider 注入。

## Non-Goals

- 不改 RAG/BOM/upload 的業務邏輯(prompt、檢索演算法、BOM mapping 規則)。
- 不引入完整 DI 容器框架(`dependency-injector` 等);用 simple thread-safe provider + 函式參數注入即可。
- 不改 async 為 sync 或反之。
- 不改 Gradio UI 的視覺/欄位;只搬位置。
- 不改 `src/config.py` 業務常數(但會新增 `REDIS_URL` 集中常數,見下方 Review fix)。
- 不做 `bom_mapper.py` 的 HEADER_MAPPING 等業務常數重構。

## Review fix 延伸(實作後 review 發現)

實作完成後的 simplify review(`docs/review-refactor-app-singletons.md`)指出 4 項可改進,經討論後納入本 change 一併處理:

1. **Redis 連線收斂與 lazy 統一**:`ui.py` module-level eager 建 Redis 連線(與「消除副作用」目標矛盾);`rag_pipeline.py`、`access_control.py` 各自再建第二、第三個連線。統一改用 `services.get_redis_conn()`,`REDIS_URL` 集中到 `config.py`。**跨 change 註**:原 Non-Goal「不改 `src/access_control.py`」在此項被刻意放寬——只改連線取得方式、不動 `access-control` spec 的任何 requirement。
2. **rag_pipeline 兩 chain 補註解**:不上鎖是刻意取捨(建構冪等低成本),以註解標明。
3. **死 import 清理**:`rag_pipeline.py` 的 `FUSION_TOP_M`、`bom_mapper.py` 的 `os`/`ChatOpenAI`。
4. **LazySingleton helper 收斂**:`services.py` 5 個純 build-once provider + `get_redis_conn` 收斂成 `LazySingleton` class,降低重複、集中鎖語意。

## Alternatives Considered

- (a) 不拆檔,只在 `app.py` 內加 lock:被否決,不解決 God Module、仍難測試。
- (b) 用 `functools.cache`/`lru_cache` 取代 singleton:被否決,`lru_cache` 在 CPython 下 GIL 保護 dict 寫入但「第一次建構期間」非原子(建構 BGE 模型時第二個請求仍會進入),不如顯式 lock 直觀可證。
- (c) 完整 DI 容器:被否決,過度工程;provider 函式 + lock 已足夠。

## Impact

- Affected specs:
  - New: `app-structure`(模組分層與 provider 線程安全的對外契約)
- Affected code:
  - New: `src/services.py`, `src/rag_pipeline.py`, `src/ui.py`, `tests/test_redis_conn.py`
  - Modified: `src/app.py`(縮減為入口)、`src/bom_mapper.py`(移除 module-level LLM + 死 import)、`src/access_control.py`(Redis 連線改用 `services.get_redis_conn`,review fix)、`src/config.py`(新增 `REDIS_URL` 常數,review fix)、`tests/test_app_pipeline.py`、`tests/test_rag_stream.py`(patch 目標調整)、`tests/test_bom_mapper.py`、`tests/test_services.py`(加 `get_redis_conn` 測試)
  - Removed: 無
