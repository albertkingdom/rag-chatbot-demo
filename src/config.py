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

# Prompt Cache Settings
CACHE_ENABLED = True
CACHE_TTL_SECONDS = 86400  # 24 hours
CACHE_SIMILARITY_THRESHOLD = 0.95  # Cosine similarity threshold for cache hits
CACHE_MAX_SEARCH_RESULTS = 5  # Max number of cache entries to check for similarity

# Hybrid Search Settings
BM25_TOP_N = 10        # Number of candidates to retrieve from BM25
VECTOR_TOP_N = 10      # Number of candidates to retrieve from vector store
RRF_K = 60             # Reciprocal Rank Fusion constant
FUSION_TOP_M = 10      # Max candidates after fusion, fed to reranker
