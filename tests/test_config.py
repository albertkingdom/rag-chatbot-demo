"""Spec: sample-knowledge-base — configurable Pinecone index name.

Each case imports the modules in a fresh interpreter so the environment is
read the way it is at process start, and the pytest process's src.config is
never reloaded.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


def resolved_names(value, module="src.config"):
    env = {k: v for k, v in os.environ.items() if k != "PINECONE_INDEX_NAME"}
    if value is not None:
        env["PINECONE_INDEX_NAME"] = value
    code = f"import {module} as m; print(m.PINECONE_INDEX_NAME)"
    out = subprocess.run(
        [sys.executable, "-c", code], cwd=REPO_ROOT, env=env,
        capture_output=True, text=True, check=True,
    )
    return out.stdout.strip().splitlines()[-1]


def test_default_index_name():
    assert resolved_names(None) == "rag-demo-qa-index"


def test_overridden_index_name():
    assert resolved_names("my-index") == "my-index"


def test_empty_value_falls_back():
    assert resolved_names("") == "rag-demo-qa-index"


@pytest.mark.parametrize("module", ["src.build_vector_store", "src.services"])
def test_consumers_use_the_overridden_name(module):
    assert resolved_names("my-index", module) == "my-index"
