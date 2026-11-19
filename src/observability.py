"""Prometheus metrics for chat requests, retrieval and reranking.

Spec: service-level-metrics (openspec change local-prometheus-monitoring).

All metrics are defined once, at module level, on the default registry. The
service runs a single uvicorn worker, so multiprocess mode is not needed, and
nothing here may be created inside a function that runs more than once (tests
build several apps in one process).

Labels are limited to ``response_source``, ``outcome`` and ``low_confidence``,
each with the fixed value set below. Session ids, user input and document
content never become label values.

Recording a metric must never change a response or fail a request: every
write goes through ``_safely`` and a failure is only logged.
"""

import logging
import time
from collections.abc import AsyncGenerator, AsyncIterator
from typing import Any

from prometheus_client import Counter, Histogram

logger = logging.getLogger("observability")

# Fixed label value sets. ``unknown`` is a stream that ended before its
# ``metadata`` event.
RESPONSE_SOURCES = ("rag", "direct", "cache", "guardrail", "unknown")
REQUEST_OUTCOMES = ("success", "error", "cancelled")
RERANK_OUTCOMES = ("ok", "timeout", "error")

# Shared by both request latency histograms. Deliberately wide so any
# candidate SLO threshold is a bucket boundary; an SLO threshold must be one
# of these values so SLI ratios are exact counts, not interpolated estimates.
LATENCY_BUCKETS = (0.5, 1, 2, 3, 5, 8, 10, 12, 15, 20, 30, 60)
RERANK_BUCKETS = (0.1, 0.25, 0.5, 1, 2, 3, 5, 8)

CHAT_REQUESTS = Counter(
    "chat_requests_total",
    "Chat stream requests that started, by response source and outcome.",
    ["response_source", "outcome"],
)
CHAT_FIRST_ANSWER_TEXT = Histogram(
    "chat_first_answer_text_seconds",
    "Seconds from stream start to the first delta event, successful requests only.",
    ["response_source"],
    buckets=LATENCY_BUCKETS,
)
CHAT_RESPONSE_DURATION = Histogram(
    "chat_response_duration_seconds",
    "Seconds from stream start to the done event, successful requests only.",
    ["response_source"],
    buckets=LATENCY_BUCKETS,
)
RAG_RETRIEVAL = Counter(
    "rag_retrieval_total",
    "Chat requests that ran retrieval, by whether the result was low confidence.",
    ["low_confidence"],
)
RAG_RETRIEVAL_RETRIES = Counter(
    "rag_retrieval_retries_total",
    "Retrieval retries performed across chat requests.",
)
RAG_RERANK_DURATION = Histogram(
    "rag_rerank_duration_seconds",
    "Seconds spent in one rerank attempt, by outcome.",
    ["outcome"],
    buckets=RERANK_BUCKETS,
)

# Create every labelled series at zero. Prometheus does not count the first
# sample of a series that appears with a non-zero value, so without this the
# first error after startup would be invisible to rate()/increase().
for _source in RESPONSE_SOURCES:
    for _outcome in REQUEST_OUTCOMES:
        CHAT_REQUESTS.labels(response_source=_source, outcome=_outcome)
    CHAT_FIRST_ANSWER_TEXT.labels(response_source=_source)
    CHAT_RESPONSE_DURATION.labels(response_source=_source)
for _flag in ("true", "false"):
    RAG_RETRIEVAL.labels(low_confidence=_flag)
for _outcome in RERANK_OUTCOMES:
    RAG_RERANK_DURATION.labels(outcome=_outcome)
del _source, _outcome, _flag

# Monotonic clock for request timing; a module attribute so tests can drive it.
_clock = time.monotonic


def _safely(what: str, record) -> None:
    """Run one metric write; a failure is logged and never propagates."""
    try:
        record()
    except Exception:
        logger.warning("Metric recording failed (%s)", what, exc_info=True)


def _known_source(value: Any) -> str:
    return value if value in RESPONSE_SOURCES else "unknown"


def _record_request(
    source: str,
    outcome: str,
    started: float,
    first_delta_at: float | None,
    done_at: float | None,
) -> None:
    """Record the single outcome of one stream. Synchronous: it runs in ``finally``."""
    _safely(
        "chat_requests_total",
        lambda: CHAT_REQUESTS.labels(response_source=source, outcome=outcome).inc(),
    )
    if outcome != "success":
        return
    if done_at is not None:
        _safely(
            "chat_response_duration_seconds",
            lambda: CHAT_RESPONSE_DURATION.labels(response_source=source).observe(done_at - started),
        )
    if first_delta_at is not None:
        _safely(
            "chat_first_answer_text_seconds",
            lambda: CHAT_FIRST_ANSWER_TEXT.labels(response_source=source).observe(first_delta_at - started),
        )


async def observe_chat_stream(events: AsyncIterator[Any]) -> AsyncGenerator[Any, None]:
    """Forward chat events unchanged while recording the request's metrics.

    Exactly one ``chat_requests_total`` increment happens in ``finally``:

    * ``error``: an ``error`` event was seen, or the inner stream raised an
      ``Exception`` (re-raised unchanged for the route to handle).
    * ``success``: a ``done`` event was seen, including a guardrail answer.
    * ``cancelled``: neither (client disconnect closes the generator, or
      ``CancelledError`` is raised into it; both are ``BaseException`` and do
      not match ``except Exception``).

    ``response_source`` comes from the ``metadata`` event, ``unknown`` when
    the stream ends before one. Latency histograms are observed only for
    ``success``; the first-answer latency also needs a ``delta`` event and is
    never measured from ``status``. The clock starts when iteration starts.
    """
    started = _clock()
    source = "unknown"
    first_delta_at: float | None = None
    done_at: float | None = None
    saw_done = False
    saw_error = False
    try:
        async for event in events:
            try:
                kind = getattr(event, "type", None)
                if kind == "delta":
                    if first_delta_at is None:
                        first_delta_at = _clock()
                elif kind == "metadata":
                    source = _known_source(getattr(event, "response_source", None))
                elif kind == "done":
                    saw_done = True
                    done_at = _clock()
                elif kind == "error":
                    saw_error = True
            except Exception:
                logger.warning("Could not observe a chat event", exc_info=True)
            yield event
    except Exception:
        saw_error = True
        raise
    finally:
        outcome = "error" if saw_error else "success" if saw_done else "cancelled"
        _record_request(source, outcome, started, first_delta_at, done_at)


def record_retrieval(*, low_confidence: bool, retries: int) -> None:
    """Count one retrieval outcome for a RAG request and its retries."""
    _safely(
        "rag_retrieval_total",
        lambda: RAG_RETRIEVAL.labels(low_confidence="true" if low_confidence else "false").inc(),
    )
    if retries > 0:
        _safely("rag_retrieval_retries_total", lambda: RAG_RETRIEVAL_RETRIES.inc(retries))


def record_rerank(outcome: str, seconds: float) -> None:
    """Observe one rerank attempt; ``outcome`` is ok, timeout or error."""
    if outcome not in RERANK_OUTCOMES:
        outcome = "error"
    _safely(
        "rag_rerank_duration_seconds",
        lambda: RAG_RERANK_DURATION.labels(outcome=outcome).observe(seconds),
    )
