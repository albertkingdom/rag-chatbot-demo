"""Thread-safe singleton providers for shared resources.

Each provider uses double-checked locking (check-then-lock-then-recheck) so
that concurrent first calls initialize exactly once, and subsequent calls
return the cached instance without contending on the lock.

Public providers:
    get_redis_conn()        -> redis.Redis
    get_embeddings()        -> OpenAIEmbeddings
    get_llm()               -> ChatOpenAI
    get_vectorstore()       -> PineconeVectorStore
    get_bm25_index()        -> BM25Index
    get_hybrid_retriever()  -> HybridRetriever
    get_reranker_model()    -> HuggingFaceCrossEncoder
    _get_optimal_device()   -> str

This module has NO import-time side effects (no network, no model loading,
no Redis connection).
"""

import logging
import os
import threading

import redis
from langchain_community.cross_encoders import HuggingFaceCrossEncoder
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from langchain_pinecone import PineconeVectorStore

from .bm25_index import BM25Index
from .config import PINECONE_INDEX_NAME, REDIS_URL
from .hybrid_retriever import HybridRetriever

logger = logging.getLogger("services")


# ---------------------------------------------------------------------------
# LazySingleton helper — used by pure build-once providers
# ---------------------------------------------------------------------------


class LazySingleton:
    """Thread-safe lazy initializer for a build-once, cache-forever value.

    Encapsulates the double-checked locking pattern so the 6 pure providers
    (redis, embeddings, llm, vectorstore, hybrid_retriever, reranker) share
    one implementation and cannot accidentally forget the lock. Providers
    with extra logic (bm25 reload_if_stale) stay hand-written.
    """

    __slots__ = ("_build", "_lock", "_value")

    def __init__(self, build):
        self._build = build
        self._lock = threading.Lock()
        self._value = None

    def get(self):
        if self._value is None:
            with self._lock:
                if self._value is None:
                    self._value = self._build()
        return self._value


# ---------------------------------------------------------------------------
# Device helper
# ---------------------------------------------------------------------------


def _get_optimal_device():
    """自動偵測最佳運算裝置（CUDA > MPS > CPU）。"""
    import torch

    if torch.cuda.is_available():
        device = "cuda"
        logger.info("Using CUDA GPU: %s", torch.cuda.get_device_name(0))
    elif hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
        device = "mps"
        logger.info("Using Apple Silicon MPS acceleration")
    else:
        device = "cpu"
        logger.info("Using CPU")
    return device


# ---------------------------------------------------------------------------
# Singletons
# ---------------------------------------------------------------------------
# 6 pure build-once providers use LazySingleton (redis, embeddings, llm,
# vectorstore, hybrid_retriever, reranker). 1 provider with extra logic
# (bm25_index reload_if_stale) stays hand-written with its own lock.

_redis_conn = LazySingleton(lambda: redis.from_url(REDIS_URL))


def get_redis_conn():
    """Single shared Redis connection (thread-safe, lazy)."""
    return _redis_conn.get()


def _build_embeddings():
    openai_api_key = os.environ.get("OPENAI_API_KEY")
    if not openai_api_key:
        raise ValueError("OPENAI_API_KEY not configured")
    return OpenAIEmbeddings(openai_api_key=openai_api_key)


_embeddings = LazySingleton(_build_embeddings)


def get_embeddings():
    return _embeddings.get()


def _build_llm():
    openrouter_api_key = os.environ.get("OPENROUTER_API_KEY")
    if not openrouter_api_key:
        raise ValueError("OPENROUTER_API_KEY not configured")
    return ChatOpenAI(
        model="google/gemini-2.5-flash",
        temperature=0,
        streaming=True,
        openai_api_key=openrouter_api_key,
        openai_api_base="https://openrouter.ai/api/v1",
    )


_llm = LazySingleton(_build_llm)


def get_llm():
    return _llm.get()


_vectorstore = LazySingleton(
    lambda: PineconeVectorStore(index_name=PINECONE_INDEX_NAME, embedding=get_embeddings())
)


def get_vectorstore():
    return _vectorstore.get()


def _build_reranker():
    logger.info("Initializing BGE-Reranker model (BAAI/bge-reranker-v2-m3)...")
    model_dir = os.environ.get("MODEL_CACHE_DIR", os.path.join(os.getcwd(), "models"))
    os.makedirs(model_dir, exist_ok=True)
    device = _get_optimal_device()
    model = HuggingFaceCrossEncoder(
        model_name="BAAI/bge-reranker-v2-m3",
        model_kwargs={"device": device, "cache_folder": model_dir},
    )
    logger.info("BGE-Reranker model initialized successfully on %s.", device)
    return model


_reranker = LazySingleton(_build_reranker)


def get_reranker_model():
    """取得底層的 CrossEncoder 模型實體（thread-safe, 只初始化一次）。"""
    return _reranker.get()


_hybrid_retriever = LazySingleton(
    lambda: HybridRetriever(bm25_index=get_bm25_index(), vector_store=get_vectorstore())
)


def get_hybrid_retriever():
    return _hybrid_retriever.get()


# --- hand-written providers (extra logic, not pure build-once) ---

_bm25_index = None
_bm25_index_lock = threading.Lock()


def get_bm25_index():
    """取得 BM25 index;若已有實例,順便 reload 若 sync Job 寫了新版本。"""
    global _bm25_index
    if _bm25_index is None:
        with _bm25_index_lock:
            if _bm25_index is None:
                _bm25_index = BM25Index()
    else:
        _bm25_index.reload_if_stale()
    return _bm25_index
