"""Spec: sample-knowledge-base — configurable Pinecone index name."""

import importlib

import pytest

from src import config


@pytest.fixture
def reload_config(monkeypatch):
    def _reload(value):
        if value is None:
            monkeypatch.delenv("PINECONE_INDEX_NAME", raising=False)
        else:
            monkeypatch.setenv("PINECONE_INDEX_NAME", value)
        return importlib.reload(config)

    yield _reload
    monkeypatch.delenv("PINECONE_INDEX_NAME", raising=False)
    importlib.reload(config)


def test_default_index_name(reload_config):
    assert reload_config(None).PINECONE_INDEX_NAME == "rag-demo-qa-index"


def test_overridden_index_name(reload_config):
    assert reload_config("my-index").PINECONE_INDEX_NAME == "my-index"


def test_empty_value_falls_back(reload_config):
    assert reload_config("").PINECONE_INDEX_NAME == "rag-demo-qa-index"
