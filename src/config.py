"""
Configuration constants for the application.
"""

# Directory for storing uploaded user manuals and other data sources
DATA_SOURCE_DIR = "./uploaded_files/"

# Name of the Pinecone index
PINECONE_INDEX_NAME = "carbon-assistant-qa-index"

# Prompt Cache Settings
CACHE_ENABLED = True
CACHE_TTL_SECONDS = 86400  # 24 hours
CACHE_SIMILARITY_THRESHOLD = 0.95  # Cosine similarity threshold for cache hits
CACHE_MAX_SEARCH_RESULTS = 5  # Max number of cache entries to check for similarity
