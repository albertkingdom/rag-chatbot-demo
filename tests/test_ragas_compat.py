"""Regression test: ragas must import with the locked langchain-community."""


def test_ragas_imports_with_compat_shim():
    from tests import ragas_compat  # noqa: F401
    from ragas import evaluate, EvaluationDataset, SingleTurnSample  # noqa: F401
    from ragas.llms import LangchainLLMWrapper  # noqa: F401
    from ragas.embeddings import LangchainEmbeddingsWrapper  # noqa: F401
    from ragas.metrics import (  # noqa: F401
        answer_relevancy,
        context_precision,
        context_recall,
        faithfulness,
    )


def test_reranker_import_still_works():
    from langchain_community.cross_encoders import HuggingFaceCrossEncoder  # noqa: F401
