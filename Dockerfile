# Use an official Python runtime as a parent image
FROM python:3.10-slim

# Set the working directory in the container
WORKDIR /app

# Copy the requirements file into the container
COPY requirements.txt .

# Install any needed packages specified in requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Pre-download the BGE reranker into the image so cold starts never fetch it.
# MODEL_CACHE_DIR is the single source of truth shared with get_reranker_model().
# Placed before COPY . . so code changes don't invalidate this heavy layer.
ENV MODEL_CACHE_DIR=/app/models
RUN python -c "import os; from huggingface_hub import snapshot_download; snapshot_download('BAAI/bge-reranker-v2-m3', cache_dir=os.environ['MODEL_CACHE_DIR'])"

# Force offline use of the cached model at runtime: no download and no
# revision HEAD check at cold start. Must come AFTER the download above.
ENV HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1

# Pre-download the jieba traditional-Chinese dictionary (dict.txt.big) so
# runtime never fetches it. JIEBA_DICT_PATH is read once at module load by
# src/bm25_index.py (jieba.set_dictionary). Placed before COPY . . so code
# changes don't invalidate this layer.
ENV JIEBA_DICT_PATH=/app/dict/dict.txt.big
RUN python -c "import os, urllib.request; os.makedirs(os.path.dirname(os.environ['JIEBA_DICT_PATH']), exist_ok=True); urllib.request.urlretrieve('https://raw.githubusercontent.com/fxsjy/jieba/master/extra_dict/dict.txt.big', os.environ['JIEBA_DICT_PATH'])"

# Copy the rest of the application's code into the container
COPY . .

# Documentation only; Cloud Run routes to $PORT. Default 8080 to match it.
EXPOSE 8080

# Run the web server. Shell form so ${PORT} is expanded at runtime:
# Cloud Run injects PORT=8080; local docker-compose leaves it unset -> 80.
# Single worker with async event loop (Gradio requires shared state);
# CPU-bound operations use asyncio.to_thread() to avoid blocking.
CMD ["sh", "-c", "uvicorn src.app:app --host 0.0.0.0 --port ${PORT:-80}"]
