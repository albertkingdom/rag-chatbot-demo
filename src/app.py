"""FastAPI entry point for the versioned API and selected frontend.

Provider functions are re-exported here for backward compatibility with
existing imports like `from src.app import get_embeddings`.
"""

import os
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .access_control import mount_auth
from .api import router as api_router

# Backward-compatible re-exports. Prefer importing from src.services directly.
from .services import (  # noqa: F401
    get_bm25_index,
    get_embeddings,
    get_hybrid_retriever,
    get_llm,
    get_reranker_model,
    get_vectorstore,
)
# Re-export retrieval/generation chain builders from rag_pipeline so callers
# that previously did `from src.app import get_retrieval_chain` still work.
from .rag_pipeline import get_retrieval_chain, get_generation_chain  # noqa: F401


def create_app() -> FastAPI:
    frontend_mode = os.environ.get("FRONTEND_MODE", "gradio").lower()
    if frontend_mode not in {"gradio", "spa"}:
        raise ValueError("FRONTEND_MODE must be 'gradio' or 'spa'")

    application = FastAPI()
    mount_auth(application, include_legacy_routes=frontend_mode == "gradio")
    application.include_router(api_router)

    if frontend_mode == "gradio":
        import gradio as gr
        from .ui import demo

        return gr.mount_gradio_app(application, demo, path="/")

    dist_dir = Path(
        os.environ.get(
            "FRONTEND_DIST_DIR",
            Path(__file__).resolve().parents[1] / "frontend" / "dist",
        )
    )
    assets_dir = dist_dir / "assets"
    if assets_dir.is_dir():
        application.mount("/assets", StaticFiles(directory=assets_dir), name="spa-assets")

    @application.get("/{path:path}", include_in_schema=False)
    async def spa_fallback(path: str):
        if path == "api" or path.startswith("api/"):
            return JSONResponse({"detail": "Not Found"}, status_code=404)
        index_file = dist_dir / "index.html"
        if not index_file.is_file():
            return JSONResponse(
                {"detail": "Frontend build is unavailable"}, status_code=503
            )
        return FileResponse(index_file)

    return application


app = create_app()


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
