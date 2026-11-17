#!/usr/bin/env python3
"""Generate baseline traffic for the local stack's latency and SLO measurements.

Sends each question in a question file, one after another, to the local
``/api/v1/chat/stream`` endpoint and prints how many requests ended in each
``response_source`` / outcome. Requests are authenticated with the
``X-API-Key`` header (``APP_API_KEY``) and carry no session cookie, so they are
not limited to one answer per session and do not touch conversation history.

It refuses to run, before sending anything, when:

* the target Pinecone index is a production index (the same list the
  evaluation script refuses),
* ``APP_API_KEY`` is not set, or
* the question file contains the same question twice.

The Pinecone index name is read the way docker compose resolves it: the shell
environment first, then the env file (the repository ``.env`` unless
``--env-file`` says otherwise), then the compose default. Existing environment
variables are never overridden by the file.

After the checks pass, a failed request only counts in the summary and the
exit status stays 0. Outcomes: ``success`` (a ``done`` event), ``error`` (an
``error`` event), ``incomplete`` (the stream ended with neither) and
``http_error`` (a non-200 response or no response at all, always counted under
``unknown``).

Usage:
    python scripts/load_baseline.py [--base-url URL] [--questions PATH]
        [--env-file PATH] [--limit N] [--delay SECONDS]

The question file is a JSON list of strings, or a JSON object whose values are
lists of strings (sent in key order).
"""

import argparse
import json
import os
import sys
import time
from collections import Counter
from pathlib import Path

import requests
from dotenv import dotenv_values

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.eval_sample_manual import PRODUCTION_INDEXES  # noqa: E402

DEFAULT_ENV_FILE = REPO_ROOT / ".env"
DEFAULT_QUESTIONS = REPO_ROOT / "scripts" / "load_baseline_questions.json"
# Same default as docker-compose.yml and src/config.py.
DEFAULT_INDEX = "rag-demo-qa-index"
STREAM_PATH = "/api/v1/chat/stream"
# Seconds: connect, then the longest silence tolerated between two stream lines.
REQUEST_TIMEOUT = (10, 180)


def log(message: str) -> None:
    print(f"[load-baseline] {message}", flush=True)


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Send baseline chat traffic to a local stack.")
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--questions", default=str(DEFAULT_QUESTIONS))
    parser.add_argument(
        "--env-file",
        default=None,
        help="env file read for PINECONE_INDEX_NAME and APP_API_KEY (default: the repository .env)",
    )
    parser.add_argument("--limit", type=int, default=None, help="send only the first N questions")
    parser.add_argument("--delay", type=float, default=1.0, help="seconds to wait between requests")
    args = parser.parse_args(argv)
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be at least 1")
    if args.delay < 0:
        parser.error("--delay must not be negative")
    return args


def normalise(text: str) -> str:
    return " ".join(text.split()).casefold()


def load_questions(path) -> tuple[list[str], str | None]:
    """Return ``(questions, problem)``; ``problem`` is None when the file is usable."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return [], f"cannot read question file {path}: {exc}"
    groups = list(data.values()) if isinstance(data, dict) else [data]
    questions = []
    for group in groups:
        if not isinstance(group, list):
            return [], f"question file {path} must hold a list of strings or an object of such lists"
        questions.extend(group)
    if not questions or not all(isinstance(q, str) and q.strip() for q in questions):
        return [], f"question file {path} must contain non-empty strings only"
    seen, repeated = set(), []
    for question in questions:
        key = normalise(question)
        if key in seen:
            repeated.append(question)
        seen.add(key)
    if repeated:
        return [], f"question file {path} has duplicate questions, for example: {repeated[0]!r}"
    return questions, None


def resolve_environment(env_file: str | None) -> tuple[dict, str | None]:
    """Shell environment over env file; an explicit env file must exist."""
    path = Path(env_file) if env_file is not None else DEFAULT_ENV_FILE
    if env_file is not None and not path.is_file():
        return {}, f"env file not found: {path}"
    file_values = dotenv_values(path) if path.is_file() else {}
    return {**file_values, **os.environ}, None


def read_stream(response) -> tuple[str, str]:
    """Read one NDJSON stream to its end; return ``(response_source, outcome)``."""
    source, outcome = "unknown", "incomplete"
    try:
        for raw in response.iter_lines():
            if not raw:
                continue
            try:
                event = json.loads(raw)
            except ValueError:
                continue
            kind = event.get("type") if isinstance(event, dict) else None
            if kind == "metadata":
                value = event.get("responseSource")
                source = value if isinstance(value, str) and value else "unknown"
            elif kind == "done" and outcome != "error":
                outcome = "success"
            elif kind == "error":
                outcome = "error"
    except requests.RequestException:
        pass  # the connection dropped mid-stream: keep what was seen so far
    return source, outcome


def send_question(http, url: str, api_key: str, question: str) -> tuple[str, str]:
    try:
        response = http.post(
            url,
            json={"message": question},
            headers={"X-API-Key": api_key},
            stream=True,
            timeout=REQUEST_TIMEOUT,
        )
    except requests.RequestException:
        return "unknown", "http_error"
    try:
        if response.status_code != 200:
            return "unknown", "http_error"
        return read_stream(response)
    finally:
        response.close()


def print_summary(counts: Counter) -> None:
    log(f"summary ({sum(counts.values())} requests):")
    for (source, outcome), number in sorted(counts.items()):
        print(f"  {source}/{outcome}: {number}", flush=True)
    if counts and all(outcome == "http_error" for _, outcome in counts):
        log("WARNING: every request ended as http_error; check --base-url and that "
            "APP_API_KEY matches the running stack.")


def main(argv=None, *, http=requests, sleep=time.sleep) -> int:
    args = parse_args(argv)

    env, problem = resolve_environment(args.env_file)
    if problem:
        log(f"ERROR: {problem}")
        return 1
    index = env.get("PINECONE_INDEX_NAME") or DEFAULT_INDEX
    log(f"target Pinecone index: {index}")
    if index in PRODUCTION_INDEXES:
        log(f"ERROR: {index!r} is a production index; refusing to send any request.")
        return 1
    api_key = env.get("APP_API_KEY") or ""
    if not api_key:
        log("ERROR: APP_API_KEY is not set (shell environment or env file); refusing to send any request.")
        return 1
    questions, problem = load_questions(args.questions)
    if problem:
        log(f"ERROR: {problem}")
        return 1
    if args.limit is not None:
        questions = questions[: args.limit]

    url = args.base_url.rstrip("/") + STREAM_PATH
    counts: Counter = Counter()
    interrupted = False
    try:
        for position, question in enumerate(questions, start=1):
            started = time.monotonic()
            source, outcome = send_question(http, url, api_key, question)
            counts[(source, outcome)] += 1
            log(f"[{position}/{len(questions)}] {source}/{outcome} {time.monotonic() - started:.1f}s")
            if position < len(questions):
                sleep(args.delay)
    except KeyboardInterrupt:
        interrupted = True
        log("interrupted; summary of the requests sent so far:")
    print_summary(counts)
    return 130 if interrupted else 0


if __name__ == "__main__":
    sys.exit(main())
