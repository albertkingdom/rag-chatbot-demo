"""Spec: sample-knowledge-base — isolated sample evaluation run.

Pinecone and the sync/evaluation steps are mocked; these tests cover the
runner's guards: credentials, index selection and the optional audit of
another index.
"""

from unittest.mock import MagicMock

import pytest

from scripts import eval_sample_manual as runner
from src import config

CREDENTIALS = ("PINECONE_API_KEY", "OPENAI_API_KEY", "OPENROUTER_API_KEY")


@pytest.fixture
def env(monkeypatch):
    for name in CREDENTIALS:
        monkeypatch.setenv(name, "fake")
    monkeypatch.setattr(config, "PINECONE_INDEX_NAME", "rag-demo-qa-index")
    return monkeypatch


@pytest.fixture
def pinecone(monkeypatch):
    """Patch the Pinecone client; ids_by_index maps index name -> list of ID snapshots."""
    pc = MagicMock()
    ids_by_index: dict[str, list[set[str]]] = {}

    def list_ids(_pc, name):
        snapshots = ids_by_index[name]
        return snapshots.pop(0) if len(snapshots) > 1 else snapshots[0]

    monkeypatch.setattr(runner, "_pinecone_client", MagicMock(return_value=pc))
    monkeypatch.setattr(runner, "list_ids", MagicMock(side_effect=list_ids))
    return pc, ids_by_index


def test_without_audit_index_never_touches_another_index(env, pinecone):
    _, ids_by_index = pinecone
    env.setattr(runner, "run_evaluation", MagicMock(return_value=0))

    assert runner.main([]) == 0
    runner.list_ids.assert_not_called()


@pytest.mark.parametrize("production", ["carbon-assistant-qa-index", "carbon-assistant-sample-index"])
def test_refuses_production_index(env, pinecone, production):
    env.setattr(config, "PINECONE_INDEX_NAME", production)
    env.setattr(runner, "run_evaluation", MagicMock(return_value=0))

    assert runner.main([]) == 1
    runner.run_evaluation.assert_not_called()


def test_refuses_when_evaluated_index_is_the_audited_index(env, pinecone):
    env.setattr(runner, "run_evaluation", MagicMock(return_value=0))

    assert runner.main(["--audit-index", "rag-demo-qa-index"]) == 1
    runner.run_evaluation.assert_not_called()
    runner.list_ids.assert_not_called()


@pytest.mark.parametrize("missing", CREDENTIALS)
@pytest.mark.parametrize("unset", [True, False], ids=["unset", "empty"])
def test_missing_or_empty_credential_fails_before_pinecone(env, pinecone, missing, unset):
    if unset:
        env.delenv(missing)
    else:
        env.setenv(missing, "")
    env.setattr(runner, "run_evaluation", MagicMock(return_value=0))

    assert runner.main(["--audit-index", "prod"]) == 1
    runner._pinecone_client.assert_not_called()
    runner.list_ids.assert_not_called()
    runner.run_evaluation.assert_not_called()


def test_audit_passes_when_ids_unchanged(env, pinecone):
    _, ids_by_index = pinecone
    ids_by_index["prod"] = [{"a", "b"}]
    env.setattr(runner, "run_evaluation", MagicMock(return_value=0))

    assert runner.main(["--audit-index", "prod"]) == 0
    assert runner.list_ids.call_count == 2


def test_audit_fails_when_ids_change(env, pinecone):
    _, ids_by_index = pinecone
    ids_by_index["prod"] = [{"a", "b"}, {"a"}]
    env.setattr(runner, "run_evaluation", MagicMock(return_value=0))

    assert runner.main(["--audit-index", "prod"]) == 1


def test_audit_runs_after_an_early_failure(env, pinecone):
    _, ids_by_index = pinecone
    ids_by_index["prod"] = [{"a"}]
    env.setattr(runner, "run_evaluation", MagicMock(return_value=1))

    assert runner.main(["--audit-index", "prod"]) == 1
    assert runner.list_ids.call_count == 2


def test_audit_runs_and_original_error_propagates_on_exception(env, pinecone):
    _, ids_by_index = pinecone
    ids_by_index["prod"] = [{"a"}]
    env.setattr(runner, "run_evaluation", MagicMock(side_effect=RuntimeError("judge failed")))

    with pytest.raises(RuntimeError, match="judge failed"):
        runner.main(["--audit-index", "prod"])
    assert runner.list_ids.call_count == 2


def test_audit_read_failure_after_a_successful_run_fails(env, pinecone):
    _, ids_by_index = pinecone
    ids_by_index["prod"] = [{"a"}]
    env.setattr(runner, "run_evaluation", MagicMock(return_value=0))
    calls = {"n": 0}

    def list_ids(_pc, name):
        calls["n"] += 1
        if calls["n"] == 2:
            raise ConnectionError("pinecone down")
        return {"a"}

    env.setattr(runner, "list_ids", list_ids)

    assert runner.main(["--audit-index", "prod"]) == 1


def test_original_exception_survives_an_audit_read_failure(env, pinecone):
    original = RuntimeError("judge failed")
    env.setattr(runner, "run_evaluation", MagicMock(side_effect=original))
    calls = {"n": 0}

    def list_ids(_pc, name):
        calls["n"] += 1
        if calls["n"] == 2:
            raise ConnectionError("pinecone down")
        return {"a"}

    env.setattr(runner, "list_ids", list_ids)

    with pytest.raises(RuntimeError) as excinfo:
        runner.main(["--audit-index", "prod"])
    assert excinfo.value is original
