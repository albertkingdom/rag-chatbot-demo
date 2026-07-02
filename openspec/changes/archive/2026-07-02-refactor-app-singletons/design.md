## Context

`src/app.py` 637 行,含 8 個 `global _x; if _x is None: _x = build()` singleton、RAG 業務、BOM/upload handler、Gradio UI、FastAPI 入口。`src/bom_mapper.py:16` 在 import 時建構 LLM。本 change 拆分模組並讓 singleton thread-safe,不改業務邏輯。`src/access_control.py` 已由前一 change 完成,不動。

## Goals / Non-Goals

**Goals:**
- `app.py` 縮減為入口;業務邏輯搬至 `rag_pipeline.py`、UI 至 `ui.py`、singleton 至 `services.py`。
- 8 個 singleton provider 用 `threading.Lock` + double-checked locking,並發首次呼叫只建構一次。
- `bom_mapper.py` 移除 module-level LLM,改 lazy 取得。
- 既有測試(調整 patch 目標後)全綠;`from src.app import get_xxx` 仍可用(re-export)。

**Non-Goals:**
- 不改 RAG/BOM/upload 業務邏輯。
- 不引入 DI 容器框架。
- 不改 async/sync 邊界。
- 不改 Gradio UI 視覺/欄位。
- 不動 `access_control.py`/`config.py`。

## Decisions

### 模組分層與職責

- `src/services.py`:8 個 singleton provider + `_get_optimal_device`,每個 thread-safe。`_embeddings`/`_llm`/... 等 module-private 狀態與 lock 都在此檔。
- `src/rag_pipeline.py`:`_QA_PROMPT`、`_format_docs`(含 `@traceable`)、`format_history`、`_parse_history_turns`、`rewrite_query`、`chat_stream`。從 `services` 取 provider,從 `config` 取常數,從 `access_control`/`conversation_db`/`guardrails`/`hybrid_retriever`/`rerank_stage` 取既有元件。
- `src/ui.py`:Gradio `gr.Blocks` UI 定義 + `bom_mapper_func` + `upload_manual_func` + `poll_status`。`demo` 變數在此定義,供 `app.py` mount。
- `src/app.py`:`app = FastAPI()` → `mount_auth(app)` → `gr.mount_gradio_app(app, ui.demo, path="/")` → `__main__`。加 re-export `from .services import *`(顯式列名)向後相容。

替代方案:
- (a) 不拆,只加 lock:不解決 God Module,被否決。
- (b) 按 RAG/BOM 分兩大塊不分 services:provider 仍散落,被否決。

### Thread-safe provider 模式

每個 singleton 用 double-checked locking:
```
_lock = threading.Lock()
_x = None
def get_x():
    global _x
    if _x is None:
        with _lock:
            if _x is None:
                _x = build()
    return _x
```
CPython GIL 不保證 `if _x is None` 與賦值原子;顯式 lock 讓「建構期間第二請求看到 None 並重入」不可能。

替代方案:
- (a) `functools.lru_cache`:GIL 保護 dict 但建構期間非原子,被否決。
- (b) 模組 import 時建構:破壞 lazy + 副作用,被否決。

### bom_mapper 副作用消除

移除 `bom_mapper.py:15-21` 的 `API_KEY = os.getenv(...)` + `llm = ChatOpenAI(...)`。`llm_batch_classify_headers`/`llm_batch_verify_headers`/`classify_bom_headers` 改為在函式內呼叫 `from .services import get_llm; llm = get_llm()`,若 `OPENROUTER_API_KEY` 未設則 `get_llm` 拋 `ValueError`(與現行 app.py 的 `get_llm` 一致)。

### 向後相容 re-export

`src/app.py` 末尾加 `from .services import (get_embeddings, get_llm, get_vectorstore, get_bm25_index, get_hybrid_retriever, get_retrieval_chain, get_generation_chain, get_reranker_model, get_intent_classifier)`(顯式列名,避免 `*` 污染命名空間)。任何 `from src.app import get_xxx` 仍可用。

### 測試 patch 目標調整

`test_app_pipeline.py` 原本 `monkeypatch.setattr(app_module, "get_bm25_index", ...)` 等,改為 patch `src.services` 模組(因 `app.py` re-export 後,`app_module.get_bm25_index` 與 `services.get_bm25_index` 是同一函式物件,patch `services` 即可影響 `rag_pipeline` 內的呼叫)。`test_rag_stream.py` 修 `from app import rag_chain` → `from src.rag_pipeline import chat_stream`(或刪除該過時腳本,因 `rag_chain` 已不存在)。

### Redis 連線收斂與 lazy 統一

實作後 review 發現:`src/ui.py` 在 module load 時 eager 建 `redis.from_url(...)`(與「消除 module-level 副作用」目標矛盾,且 GCP 模式下該連線沒人用);`src/rag_pipeline.py` 與 `src/access_control.py` 各自再 lazy/eager 建第二、第三個連線。同一個 `REDIS_URL` 散落三處各自 `os.getenv`,也與 `config.py` 集中管理 env var 的慣例不一致。

決定:`config.py` 新增 `REDIS_URL` 常數;`services.py` 新增 `get_redis_conn()`(比照其他 provider 的 lazy-singleton + lock 風格);`ui.py`、`rag_pipeline.py`、`access_control.py` 三處的 `redis.from_url(...)` 全改叫 `services.get_redis_conn()`。`ui.py` 的 module-level `_conn` 與 `q = Queue(...)` 改成 lazy(只在 local 模式且首次被 handler 呼叫時建),消除 import 時的網路連線副作用。

範圍註:`access_control.py` 原屬已 archived 的 `add-access-control` change;本次為了「單一 Redis 連線來源」的一致性目標,刻意跨 change 修改其 `build_auth_config` 內的連線建構,改為呼叫 `services.get_redis_conn()`。這不變更 `access-control` spec 的任何 requirement(認證/限流/ session 行為不變),只改連線取得方式。

### rag_pipeline 兩個 chain 不上鎖的取捨

`get_retrieval_chain()` / `get_generation_chain()` 在 `rag_pipeline.py` 用裸 `if _x is None: _x = build()`,無 lock。這與 `services.py` 7 個上鎖 singleton 不一致,但經評估後**刻意不上鎖**:這兩個函式只是把已被 `services.py` 鎖保護過的 singleton(`get_hybrid_retriever()`、`get_llm()`、`get_reranker_model()`)包進 `RunnableLambda` pipe,建構過程無網路/模型載入/寫檔,冪等且微秒級成本;race 最壞結果是多建一個功能等價的 pipe 物件(隨即被 GC),不會重複載入昂貴資源。以一行註解標明此取捨,而非加鎖。

### LazySingleton helper 收斂

`services.py` 7 個 double-checked-locking block 高度重複。其中 5 個純「build once, cache」(`get_embeddings`/`get_llm`/`get_vectorstore`/`get_hybrid_retriever`/`get_reranker_model`)收斂成小型 `LazySingleton` helper class(`__init__(build)` + `get()` 內含 double-checked locking)。`get_bm25_index`(多了 `reload_if_stale()` 分支)與 `get_intent_classifier`(缺 key 回 `None` 且不快取)有額外邏輯,維持手寫不硬套 helper。加上 `get_redis_conn`(純 build-once)也用 helper。helper 降低重複且讓「上鎖」語意集中一處,日後新增純 singleton 直接用 helper 不會漏鎖。

### 死 import 清理

`rag_pipeline.py` 沿用自舊 `app.py` 的 `FUSION_TOP_M` import 未被引用;`bom_mapper.py` 在移除 module-level LLM 後 `os` 與 `ChatOpenAI` 已無人使用。直接刪除,風險極低。

## Implementation Contract

**行為(對外可觀察):**
- `import src.app` / `import src.bom_mapper` / `import src.ui` 不觸發網路/模型載入/Redis 連線。
- `from src.app import get_embeddings` 仍可 import(re-export)。
- 並發呼叫 `get_reranker_model()` 只建構一次。
- RAG chat / BOM mapping / upload 行為與重構前一致(同 prompt、同流程、同回應)。
- 既有測試(調整 patch 目標後)全綠。
- Redis 連線全 app 只有一個來源(`services.get_redis_conn()`),`ui.py`/`rag_pipeline.py`/`access_control.py` 不再各自 `redis.from_url(...)`。

**介面 / 資料形狀:**
- New: `src/services.py`、`src/rag_pipeline.py`、`src/ui.py`。
- Modified: `src/app.py`(縮減)、`src/bom_mapper.py`(移除 module-level LLM)、`tests/test_app_pipeline.py`(patch 目標)、`tests/test_rag_stream.py`(import 修正或刪除)、`tests/test_bom_mapper.py`(若依賴 module-level llm 則調整)。
- `src/services.py` 公開:`get_embeddings`、`get_llm`、`get_vectorstore`、`get_bm25_index`、`get_hybrid_retriever`、`get_retrieval_chain`、`get_generation_chain`、`get_reranker_model`、`get_intent_classifier`、`_get_optimal_device`。

**失敗模式:**
- provider 缺 key → 拋 `ValueError`(與現行一致)。
- 並發首次呼叫 → lock 保證只建構一次。
- `import bom_mapper` 無 key → 不爆(副作用已移除);呼叫 `classify_bom_headers` 時才爆。

**驗收條件:**
- `wc -l src/app.py` < 50 行(只剩入口 + re-export)。
- `grep -E "^_(reranker|embeddings|llm|vectorstore|retrieval_chain|generation_chain|bm25_index|hybrid_retriever|intent_classifier) = None" src/app.py` 無結果(singleton 已搬走)。
- `grep -E "^llm = |^API_KEY = " src/bom_mapper.py` 無結果(module-level LLM 已移除)。
- `import src.bom_mapper` 在無 `OPENROUTER_API_KEY` 下不拋例外。
- `venv/bin/python -m pytest tests/ -v` 全綠(含調整後的 patch 目標)。
- 並發測試:兩 thread 呼叫 `get_reranker_model()` 拿到同一實例(以 `id()` 驗證)。

**範圍邊界:**
- In scope:`src/services.py`、`src/rag_pipeline.py`、`src/ui.py`、`src/app.py`、`src/bom_mapper.py`、相關測試調整。
- Out of scope:RAG/BOM/upload 業務邏輯、`access_control.py`、`config.py`、Gradio UI 視覺、DI 容器、async/sync 邊界。

## Risks / Trade-offs

- [re-export 讓 `app.py` 仍可被 import,但鼓勵直接從 `services` import] → 文件/註解標明 `from src.services import` 為正規路徑;re-export 為過渡。
- [double-checked locking 在 CPython 是否必要] → GIL 不保證建構期間的 check-then-set 原子性,顯式 lock 是可證的保險,效能成本可忽略(lock 只在首次建構時競爭)。
- [拆檔可能引入循環 import] → 分層 services→rag_pipeline→ui→app 單向依賴;`rag_pipeline` 與 `ui` 只 import `services`,不互相 import;`app.py` import `ui` 與 `services`。
- [測試 patch 目標改動可能遺漏] → 跑完整 `pytest tests/ -v` 驗證;逐一檢查 `monkeypatch.setattr` 目標。
