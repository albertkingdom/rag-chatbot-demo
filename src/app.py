"""FastAPI entry point.

Mounts access control (auth middleware + /health, /login, /logout) and the
Gradio UI. All business logic, singletons, and UI definitions live in
dedicated modules (src.services, src.rag_pipeline, src.ui).

Provider functions are re-exported here for backward compatibility with
existing imports like `from src.app import get_embeddings`.
"""

import uvicorn
import gradio as gr
from fastapi import FastAPI

from .access_control import mount_auth
from .ui import demo

# Backward-compatible re-exports. Prefer importing from src.services directly.
from .services import (  # noqa: F401
    get_bm25_index,
    get_embeddings,
    get_hybrid_retriever,
    get_intent_classifier,
    get_llm,
    get_reranker_model,
    get_vectorstore,
)
# Re-export retrieval/generation chain builders from rag_pipeline so callers
# that previously did `from src.app import get_retrieval_chain` still work.
from .rag_pipeline import get_retrieval_chain, get_generation_chain  # noqa: F401


app = FastAPI()
# Mount access control BEFORE gr.mount_gradio_app so the middleware wraps
# Gradio routes too.
mount_auth(app)
app = gr.mount_gradio_app(app, demo, path="/")


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
