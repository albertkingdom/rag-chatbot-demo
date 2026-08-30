"""
Configuration constants for the application.
"""

import os

# Directory for storing uploaded user manuals and other data sources.
# Local default keeps the relative path; on GCP, Cloud Run sets this to a
# subdirectory of the shared GCS volume (e.g. /mnt/data/uploaded_files).
DATA_SOURCE_DIR = os.environ.get("DATA_SOURCE_DIR", "./uploaded_files/")

# Directory where the BM25 index version files and the pointer file live.
# Local default is "models"; on GCP it points under the shared GCS volume
# (e.g. /mnt/data/models) so the Service and Job share the same index.
BM25_INDEX_DIR = os.environ.get("BM25_INDEX_DIR", "models")

# How many BM25 version files to retain after each sync (older ones pruned).
BM25_VERSION_RETENTION = int(os.environ.get("BM25_VERSION_RETENTION", "3"))

# Name of the Pinecone index
PINECONE_INDEX_NAME = "carbon-assistant-qa-index"

# Redis connection URL. Single source of truth — consumed by services.get_redis_conn()
# and indirectly by ui.py / rag_pipeline.py / access_control.py. Do not re-read
# this env var in consumers; import REDIS_URL from here instead.
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

# Prompt Cache Settings
CACHE_ENABLED = True
CACHE_TTL_SECONDS = 86400  # 24 hours
CACHE_SIMILARITY_THRESHOLD = 0.95  # Cosine similarity threshold for cache hits
CACHE_MAX_SEARCH_RESULTS = 5  # Max number of cache entries to check for similarity

# Agentic RAG: retrieval grading & retry
GRADE_SCORE_THRESHOLD = float(os.environ.get("GRADE_SCORE_THRESHOLD", "0.3"))
RETRIEVAL_MAX_RETRIES = int(os.environ.get("RETRIEVAL_MAX_RETRIES", "1"))

# Hybrid Search Settings
BM25_TOP_N = 10        # Number of candidates to retrieve from BM25
VECTOR_TOP_N = 10      # Number of candidates to retrieve from vector store
RRF_K = 60             # Reciprocal Rank Fusion constant
FUSION_TOP_M = 5       # Max candidates after fusion, fed to reranker (halved 10->5 to cut reranker inference time)

# Access Control Settings
# Toggle for auth/rate-limiting; defaults to True so production is locked down.
# Set AUTH_ENABLED=false or leave APP_API_KEY unset for local dev / hermetic tests.
AUTH_ENABLED = os.environ.get("AUTH_ENABLED", "true").lower() == "true"
# Shared API key presented via X-API-Key header or the /login form. When auth is
# enabled but this is unset, build_auth_config() degrades to disabled (see
# src/access_control.py) instead of silently rejecting every request.
APP_API_KEY = os.environ.get("APP_API_KEY")
# Per-API-key request rate limit (requests per minute). The bucket key is the
# first 16 hex chars of sha256(api_key) so all sessions from one key share one
# quota.
RATE_LIMIT_RPM = int(os.environ.get("RATE_LIMIT_RPM", "60"))
# TTL for Redis-backed session tokens issued by POST /login (seconds).
SESSION_TTL_SECONDS = int(os.environ.get("SESSION_TTL_SECONDS", "86400"))

# Chat History Settings (short-term, Redis-backed multi-turn context)
# Idle TTL for a session's stored chat history (seconds). Refreshed on each
# new turn; expired history falls back to client-supplied history.
CHAT_HISTORY_TTL_SECONDS = int(os.environ.get("CHAT_HISTORY_TTL_SECONDS", "1800"))
# Max number of most-recent conversation turns retained per session.
CHAT_HISTORY_MAX_TURNS = int(os.environ.get("CHAT_HISTORY_MAX_TURNS", "3"))
