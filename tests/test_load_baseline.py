"""Spec: load-baseline-script — safety checks, sequential requests and summary.

The script is loaded by path (scripts/ is not a package) and is driven through
``main`` with a fake HTTP client, so nothing here touches a network. Every test
points ``--env-file`` at a file under tmp_path: the developer's real ``.env``
is never read, and CI (which has none) behaves the same.
"""

import importlib.util
import json
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "load_baseline.py"
QUESTIONS = REPO_ROOT / "scripts" / "load_baseline_questions.json"
EVAL_DATA = REPO_ROOT / "tests" / "rag_eval_data.json"
PRODUCTION_INDEXES = ("carbon-assistant-qa-index", "carbon-assistant-sample-index")

_spec = importlib.util.spec_from_file_location("load_baseline", SCRIPT)
load_baseline = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(load_baseline)


class FakeResponse:
    def __init__(self, events=(), status=200):
        self.status_code = status
        self._lines = [json.dumps(event).encode() for event in events]
        self.closed = False

    def iter_lines(self, **kwargs):
        yield from self._lines

    def close(self):
        self.closed = True


class FakeHttp:
    """Stands in for the ``requests`` module: records calls, replays responses."""

    def __init__(self, *responses, on_post=None):
        self.responses = list(responses)
        self.calls = []
        self.on_post = on_post

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if self.on_post:
            self.on_post()
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def metadata(source):
    return {"type": "metadata", "elapsedMs": 5, "responseSource": source, "cacheHit": source == "cache"}


def completed(source):
    return FakeResponse([{"type": "status", "stage": "retrieving", "message": "x"},
                         {"type": "delta", "text": "答"}, metadata(source), {"type": "done"}])


def failed():
    return FakeResponse([{"type": "status", "stage": "retrieving", "message": "x"},
                         {"type": "error", "code": "internal_error", "message": "x"}])


@pytest.fixture
def env(monkeypatch, tmp_path):
    """A clean environment plus a helper that writes the env file and question file."""
    for name in ("APP_API_KEY", "PINECONE_INDEX_NAME"):
        monkeypatch.setenv(name, "placeholder")  # so monkeypatch restores the original on teardown
        monkeypatch.delenv(name)

    class Env:
        def env_file(self, **values):
            path = tmp_path / "test.env"
            path.write_text("".join(f"{key}={value}\n" for key, value in values.items()))
            return str(path)

        def questions(self, *items):
            path = tmp_path / "questions.json"
            path.write_text(json.dumps(list(items), ensure_ascii=False))
            return str(path)

        def run(self, http, *, env_file=None, questions=None, extra=(), sleep=None):
            args = ["--env-file", env_file or self.env_file(APP_API_KEY="test-key"),
                    "--questions", questions or self.questions("q1", "q2", "q3"),
                    "--delay", "0", *extra]
            return load_baseline.main(args, http=http, sleep=sleep or (lambda _: None))

    return Env()


# --- preconditions: non-zero exit, no request sent ---------------------------


@pytest.mark.parametrize("index", PRODUCTION_INDEXES)
def test_production_index_in_the_environment_is_refused(env, monkeypatch, index, capsys):
    monkeypatch.setenv("PINECONE_INDEX_NAME", index)
    http = FakeHttp()

    assert env.run(http) != 0
    assert http.calls == []
    assert index in capsys.readouterr().out


@pytest.mark.parametrize("index", PRODUCTION_INDEXES)
def test_production_index_set_only_in_the_env_file_is_refused(env, index):
    http = FakeHttp()

    assert env.run(http, env_file=env.env_file(APP_API_KEY="test-key", PINECONE_INDEX_NAME=index)) != 0
    assert http.calls == []


def test_shell_environment_wins_over_the_env_file(env, monkeypatch, capsys):
    monkeypatch.setenv("PINECONE_INDEX_NAME", "rag-demo-qa-index")
    http = FakeHttp(completed("rag"), completed("rag"), completed("rag"))

    status = env.run(http, env_file=env.env_file(APP_API_KEY="test-key", PINECONE_INDEX_NAME=PRODUCTION_INDEXES[0]))

    assert status == 0
    assert "rag-demo-qa-index" in capsys.readouterr().out


def test_missing_api_key_is_refused(env):
    http = FakeHttp()

    assert env.run(http, env_file=env.env_file()) != 0
    assert http.calls == []


def test_empty_api_key_is_refused(env):
    http = FakeHttp()

    assert env.run(http, env_file=env.env_file(APP_API_KEY="")) != 0
    assert http.calls == []


def test_api_key_can_come_from_the_env_file(env):
    http = FakeHttp(completed("rag"), completed("rag"), completed("rag"))

    assert env.run(http, env_file=env.env_file(APP_API_KEY="from-file")) == 0
    assert http.calls[0][1]["headers"]["X-API-Key"] == "from-file"


def test_duplicate_questions_are_refused(env):
    http = FakeHttp()

    assert env.run(http, questions=env.questions("如何登入？", "別的問題", "如何登入？")) != 0
    assert http.calls == []


def test_questions_differing_only_in_case_or_spacing_count_as_duplicates(env):
    http = FakeHttp()

    assert env.run(http, questions=env.questions("How  do I log in?", " how do i LOG in? ")) != 0
    assert http.calls == []


def test_duplicates_beyond_the_limit_are_still_refused(env):
    http = FakeHttp()

    assert env.run(http, questions=env.questions("a", "b", "a"), extra=("--limit", "2")) != 0
    assert http.calls == []


def test_explicit_env_file_that_does_not_exist_is_refused(env, tmp_path):
    # A mistyped path would silently fall back to the default index while the
    # stack reads the real .env.
    http = FakeHttp()

    assert env.run(http, env_file=str(tmp_path / "missing.env")) != 0
    assert http.calls == []


def test_missing_question_file_is_refused(env, tmp_path):
    http = FakeHttp()

    assert env.run(http, questions=str(tmp_path / "missing.json")) != 0
    assert http.calls == []


def test_target_index_is_printed_before_the_first_request(env, capsys):
    seen = []
    http = FakeHttp(completed("rag"), completed("rag"), completed("rag"),
                    on_post=lambda: seen.append(capsys.readouterr().out))

    env.run(http, env_file=env.env_file(APP_API_KEY="test-key", PINECONE_INDEX_NAME="my-demo-index"))

    assert "my-demo-index" in seen[0]


def test_index_defaults_to_the_compose_default_when_unset(env, capsys):
    http = FakeHttp(completed("rag"), completed("rag"), completed("rag"))

    env.run(http)

    assert "rag-demo-qa-index" in capsys.readouterr().out


# --- requests ------------------------------------------------------------------


def test_each_question_is_one_authenticated_stream_request_without_cookies(env):
    http = FakeHttp(completed("rag"), completed("rag"), completed("rag"))

    env.run(http, extra=("--base-url", "http://localhost:9999/"))

    assert [url for url, _ in http.calls] == ["http://localhost:9999/api/v1/chat/stream"] * 3
    assert [kwargs["json"] for _, kwargs in http.calls] == [{"message": "q1"}, {"message": "q2"}, {"message": "q3"}]
    for _, kwargs in http.calls:
        assert kwargs["headers"]["X-API-Key"] == "test-key"
        assert "Cookie" not in kwargs["headers"] and "cookies" not in kwargs
        assert kwargs["stream"] is True


def test_default_base_url_and_delay():
    args = load_baseline.parse_args([])

    assert args.base_url == "http://localhost:8000"
    assert args.delay == 1.0


def test_waits_the_configured_delay_between_requests_only(env):
    http = FakeHttp(completed("rag"), completed("rag"), completed("rag"))
    waits = []

    env.run(http, extra=("--delay", "2.5"), sleep=waits.append)

    assert waits == [2.5, 2.5]


def test_each_stream_is_read_to_the_end_and_closed_before_the_next_request(env):
    responses = [completed("rag"), completed("rag"), completed("rag")]
    closed_when_next_sent = []
    http = FakeHttp(*responses, on_post=lambda: closed_when_next_sent.append([r.closed for r in responses]))

    env.run(http)

    assert closed_when_next_sent == [[False] * 3, [True, False, False], [True, True, False]]


def test_limit_sends_only_the_first_questions(env):
    http = FakeHttp(completed("rag"), completed("rag"))

    env.run(http, extra=("--limit", "2"))

    assert [kwargs["json"]["message"] for _, kwargs in http.calls] == ["q1", "q2"]


def test_question_file_may_group_questions_under_keys(env, tmp_path):
    path = tmp_path / "grouped.json"
    path.write_text(json.dumps({"manual": ["a", "b"], "offtopic": ["c"]}))
    http = FakeHttp(completed("rag"), completed("rag"), completed("direct"))

    assert env.run(http, questions=str(path)) == 0
    assert [kwargs["json"]["message"] for _, kwargs in http.calls] == ["a", "b", "c"]


# --- summary and exit status ------------------------------------------------------


def summary_counts(output):
    return {m.group(1): int(m.group(2)) for m in re.finditer(r"^\s*(\w+/\w+): (\d+)$", output, re.MULTILINE)}


def test_summary_counts_requests_per_source_and_outcome(env, capsys):
    http = FakeHttp(completed("rag"), completed("rag"), completed("cache"))

    status = env.run(http)

    assert status == 0
    assert summary_counts(capsys.readouterr().out) == {"rag/success": 2, "cache/success": 1}


def test_http_429_is_counted_as_unknown_http_error_and_the_run_continues(env, capsys):
    http = FakeHttp(completed("rag"), FakeResponse(status=429), completed("rag"))

    status = env.run(http)

    assert status == 0
    assert len(http.calls) == 3
    assert summary_counts(capsys.readouterr().out) == {"rag/success": 2, "unknown/http_error": 1}


def test_connection_failure_is_an_http_error_and_the_run_continues(env, capsys):
    import requests

    http = FakeHttp(requests.ConnectionError("refused"), completed("rag"), completed("rag"))

    assert env.run(http) == 0
    assert summary_counts(capsys.readouterr().out) == {"rag/success": 2, "unknown/http_error": 1}


def test_error_event_is_counted_under_unknown_and_the_run_continues(env, capsys):
    http = FakeHttp(completed("rag"), failed(), completed("rag"))

    assert env.run(http) == 0
    assert len(http.calls) == 3
    assert summary_counts(capsys.readouterr().out) == {"rag/success": 2, "unknown/error": 1}


def test_error_after_the_metadata_event_is_counted_under_its_source(env, capsys):
    response = FakeResponse([metadata("rag"), {"type": "error", "code": "internal_error", "message": "x"}])
    http = FakeHttp(response, completed("rag"), completed("rag"))

    env.run(http)

    assert summary_counts(capsys.readouterr().out) == {"rag/error": 1, "rag/success": 2}


def test_stream_that_ends_without_done_or_error_is_incomplete(env, capsys):
    http = FakeHttp(FakeResponse([{"type": "status", "stage": "retrieving", "message": "x"}]),
                    completed("rag"), completed("rag"))

    env.run(http)

    assert summary_counts(capsys.readouterr().out) == {"unknown/incomplete": 1, "rag/success": 2}


def test_malformed_lines_are_ignored(env, capsys):
    response = FakeResponse([metadata("rag"), {"type": "done"}])
    response._lines.insert(0, b"not json")
    response._lines.insert(1, b"")
    http = FakeHttp(response, completed("rag"), completed("rag"))

    env.run(http)

    assert summary_counts(capsys.readouterr().out) == {"rag/success": 3}


def test_exit_status_is_zero_even_when_every_request_failed(env):
    http = FakeHttp(FakeResponse(status=401), FakeResponse(status=401), FakeResponse(status=401))

    assert env.run(http) == 0


def test_warns_when_every_request_was_an_http_error(env, capsys):
    http = FakeHttp(FakeResponse(status=401), FakeResponse(status=401), FakeResponse(status=401))

    env.run(http)

    assert "WARNING" in capsys.readouterr().out


def test_no_warning_when_some_requests_succeeded(env, capsys):
    http = FakeHttp(FakeResponse(status=429), completed("rag"), completed("rag"))

    env.run(http)

    assert "WARNING" not in capsys.readouterr().out


def test_the_api_key_is_never_printed(env, capsys):
    http = FakeHttp(completed("rag"), completed("rag"), completed("rag"))

    env.run(http, env_file=env.env_file(APP_API_KEY="super-secret-key"))

    assert "super-secret-key" not in capsys.readouterr().out


# --- the bundled question file (task 5.2) -----------------------------------------


def normalise(text):
    return re.sub(r"\s+", " ", text).strip().casefold()


def bundled_groups():
    return json.loads(QUESTIONS.read_text())


def test_bundled_questions_have_enough_manual_and_offtopic_questions():
    groups = bundled_groups()

    assert len(groups["manual"]) >= 60
    assert len(groups["offtopic"]) >= 10


def test_bundled_questions_have_no_duplicates():
    questions = [normalise(q) for group in bundled_groups().values() for q in group]

    assert len(questions) == len(set(questions))


def test_bundled_questions_do_not_repeat_the_evaluation_questions():
    evaluation = {normalise(case["question"]) for case in json.loads(EVAL_DATA.read_text())}
    questions = {normalise(q) for group in bundled_groups().values() for q in group}

    assert questions & evaluation == set()


def test_bundled_questions_load_without_the_duplicate_refusal(env):
    questions, problem = load_baseline.load_questions(QUESTIONS)

    assert problem is None
    assert len(questions) == sum(len(group) for group in bundled_groups().values())


def test_manual_questions_route_to_rag_and_offtopic_ones_do_not():
    # Rule stage only (no LLM): manual questions must carry a knowledge-base
    # keyword so they reach retrieval, and the off-topic ones must not.
    from src.query_router import _rule_route

    groups = bundled_groups()

    assert [q for q in groups["manual"] if _rule_route(q) != "rag"] == []
    assert [q for q in groups["offtopic"] if _rule_route(q) == "rag"] == []


def test_the_production_index_list_is_the_one_the_evaluation_script_uses():
    from scripts import eval_sample_manual

    assert load_baseline.PRODUCTION_INDEXES == eval_sample_manual.PRODUCTION_INDEXES
