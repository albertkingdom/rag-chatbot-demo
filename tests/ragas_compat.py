"""Compatibility shim so ragas can be imported alongside langchain-community 0.4.

ragas (0.2.x through 0.4.3) imports ``ChatVertexAI`` from
``langchain_community.chat_models.vertexai`` and ``VertexAI`` from
``langchain_community.llms`` at import time, only to use them in
``isinstance`` checks. langchain-community 0.4 removed both. Downgrading
langchain-community is not an option: 0.3.x imports ``langchain.retrievers``,
which langchain 1.x removed, and that breaks the BGE reranker in
``src/services.py``.

This module registers inert stand-in classes for the two missing names. The
project never uses Vertex AI, so the isinstance checks simply never match.
Import it before importing ragas; remove it once ragas drops the import.
"""
import sys
import types

_MODULE = "langchain_community.chat_models.vertexai"


class _VertexAIUnavailable:
    """Placeholder for the removed Vertex AI classes; never instantiated."""


def install() -> None:
    """Register the stand-ins if langchain-community no longer provides them."""
    try:
        __import__(_MODULE)
    except ImportError:
        stub = types.ModuleType(_MODULE)
        stub.ChatVertexAI = _VertexAIUnavailable
        sys.modules[_MODULE] = stub

    import langchain_community.llms as llms

    try:
        getattr(llms, "VertexAI")
    except (AttributeError, ImportError):
        llms.VertexAI = _VertexAIUnavailable


install()
