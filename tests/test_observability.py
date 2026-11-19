"""Spec: service-level-metrics — request outcome, latency and bounded labels.

The metrics live on the default registry and are never reset, so every test
compares samples before and after instead of reading absolute values. Time is
driven through ``observability._clock``; nothing here sleeps or touches a
network.
"""

import asyncio

import pytest
from prometheus_client import REGISTRY

from src import observability as obs
from src.chat_events import (
    DeltaEvent,
    DoneEvent,
    ErrorEvent,
    MetadataEvent,
    SourcesEvent,
    StatusEvent,
)

REQUESTS = "chat_requests_total"
LATENCY_COUNTS = (
    "chat_first_answer_text_seconds_count",
    "chat_response_duration_seconds_count",
)


def snapshot(*names):
    """Every sample of the named series as {(name, sorted labels): value}."""
    found = {}
    for family in REGISTRY.collect():
        for sample in family.samples:
            if sample.name in names:
                found[(sample.name, tuple(sorted(sample.labels.items())))] = sample.value
    return found


def delta_between(before, after):
    keys = set(before) | set(after)
    return {
        key: after.get(key, 0.0) - before.get(key, 0.0)
        for key in keys
        if after.get(key, 0.0) != before.get(key, 0.0)
    }


def request_key(source, outcome):
    return (REQUESTS, (("outcome", outcome), ("response_source", source)))


def value(name, **labels):
    return REGISTRY.get_sample_value(name, labels) or 0.0


def meta(source):
    return MetadataEvent(elapsed_ms=1, response_source=source, cache_hit=False)


STATUS = StatusEvent(stage="retrieving", message="搜尋中")
DELTA = DeltaEvent(text="回答")
DONE = DoneEvent()


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


@pytest.fixture
def clock(monkeypatch):
    fake = Clock()
    monkeypatch.setattr(obs, "_clock", fake)
    return fake


async def stream(*steps, clock=None, then=None):
    """Fake chat event generator.

    A step is an event, or ``(seconds, event)`` to advance the fake clock
    before yielding it. ``then`` is raised after the last step.
    """
    for step in steps:
        if isinstance(step, tuple):
            seconds, event = step
            clock.now += seconds
        else:
            event = step
        yield event
    if then is not None:
        raise then


async def drain(events):
    return [event async for event in obs.observe_chat_stream(events)]


# --- definitions (task 2.1) -----------------------------------------------


def series_bounds(histogram):
    return sorted(
        float(sample.labels["le"])
        for family in REGISTRY.collect()
        for sample in family.samples
        if sample.name == f"{histogram}_bucket"
    )


def test_request_latency_histograms_share_the_wide_bucket_boundaries():
    expected = [0.5, 1, 2, 3, 5, 8, 10, 12, 15, 20, 30, 60]
    for histogram in ("chat_first_answer_text_seconds", "chat_response_duration_seconds"):
        assert sorted(set(series_bounds(histogram))) == expected + [float("inf")]
    assert obs.LATENCY_BUCKETS == tuple(expected)


def test_rerank_histogram_buckets():
    expected = [0.1, 0.25, 0.5, 1, 2, 3, 5, 8]
    assert sorted(set(series_bounds("rag_rerank_duration_seconds"))) == expected + [float("inf")]
    assert obs.RERANK_BUCKETS == tuple(expected)


@pytest.mark.parametrize(
    "series, labels",
    [
        ("chat_requests_total", {"response_source", "outcome"}),
        ("chat_first_answer_text_seconds_count", {"response_source"}),
        ("chat_response_duration_seconds_count", {"response_source"}),
        ("rag_retrieval_total", {"low_confidence"}),
        ("rag_retrieval_retries_total", set()),
        ("rag_rerank_duration_seconds_count", {"outcome"}),
    ],
)
def test_each_metric_uses_only_its_bounded_labels(series, labels):
    found = [
        set(sample.labels)
        for family in REGISTRY.collect()
        for sample in family.samples
        if sample.name == series
    ]
    assert found, f"{series} is not exposed"
    assert all(names == labels for names in found)


def test_every_label_combination_exists_before_the_first_request():
    # The first sample of a series that appears at a non-zero value is not
    # counted by rate()/increase(), so the series must start at zero.
    for source in ("rag", "direct", "cache", "guardrail", "unknown"):
        for outcome in ("success", "error", "cancelled"):
            assert REGISTRY.get_sample_value(
                REQUESTS, {"response_source": source, "outcome": outcome}
            ) is not None
    for flag in ("true", "false"):
        assert REGISTRY.get_sample_value("rag_retrieval_total", {"low_confidence": flag}) is not None
    for outcome in ("ok", "timeout", "error"):
        assert REGISTRY.get_sample_value(
            "rag_rerank_duration_seconds_count", {"outcome": outcome}
        ) is not None


def test_building_the_app_twice_does_not_duplicate_metrics(monkeypatch, tmp_path):
    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.setenv("FRONTEND_DIST_DIR", str(tmp_path))
    from src.app import create_app

    create_app()
    create_app()  # would raise "Duplicated timeseries" if metrics were built here


# --- outcome classification (task 2.2) -------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "events, source, outcome",
    [
        ([DELTA, meta("rag"), DONE], "rag", "success"),
        ([DELTA, meta("guardrail"), DONE], "guardrail", "success"),
        ([STATUS, ErrorEvent()], "unknown", "error"),
    ],
    ids=["rag-done", "guardrail-done", "error-event"],
)
async def test_completed_streams_record_exactly_one_outcome(events, source, outcome):
    before = snapshot(REQUESTS)

    forwarded = await drain(stream(*events))

    assert forwarded == events
    assert delta_between(before, snapshot(REQUESTS)) == {request_key(source, outcome): 1.0}


@pytest.mark.asyncio
async def test_exception_before_any_event_is_an_error_and_is_reraised():
    before = snapshot(REQUESTS)

    with pytest.raises(RuntimeError, match="boom"):
        await drain(stream(then=RuntimeError("boom")))

    assert delta_between(before, snapshot(REQUESTS)) == {request_key("unknown", "error"): 1.0}


@pytest.mark.asyncio
async def test_exception_after_events_is_an_error_with_the_known_source():
    before = snapshot(REQUESTS, *LATENCY_COUNTS)

    with pytest.raises(RuntimeError):
        await drain(stream(DELTA, meta("rag"), then=RuntimeError("boom")))

    assert delta_between(before, snapshot(REQUESTS, *LATENCY_COUNTS)) == {
        request_key("rag", "error"): 1.0
    }


@pytest.mark.asyncio
async def test_client_disconnect_closing_the_stream_is_cancelled():
    before = snapshot(REQUESTS)
    wrapper = obs.observe_chat_stream(stream(STATUS, DELTA, DONE))

    assert await wrapper.__anext__() == STATUS
    await wrapper.aclose()

    assert delta_between(before, snapshot(REQUESTS)) == {request_key("unknown", "cancelled"): 1.0}


@pytest.mark.asyncio
async def test_cancelled_error_raised_into_the_stream_is_cancelled_not_error():
    before = snapshot(REQUESTS)

    with pytest.raises(asyncio.CancelledError):
        await drain(stream(STATUS, then=asyncio.CancelledError()))

    assert delta_between(before, snapshot(REQUESTS)) == {request_key("unknown", "cancelled"): 1.0}


@pytest.mark.asyncio
async def test_client_leaving_right_after_done_still_counts_as_success():
    before = snapshot(REQUESTS)
    wrapper = obs.observe_chat_stream(stream(DELTA, meta("rag"), DONE))

    seen = [await wrapper.__anext__() for _ in range(3)]
    assert seen[-1] == DONE
    await wrapper.aclose()

    assert delta_between(before, snapshot(REQUESTS)) == {request_key("rag", "success"): 1.0}


# --- latency histograms (task 2.2) -----------------------------------------


@pytest.mark.asyncio
async def test_status_event_does_not_count_as_first_answer_text(clock):
    first_sum = value("chat_first_answer_text_seconds_sum", response_source="rag")
    total_sum = value("chat_response_duration_seconds_sum", response_source="rag")
    first_count = value("chat_first_answer_text_seconds_count", response_source="rag")
    below_8 = value("chat_first_answer_text_seconds_bucket", response_source="rag", le="8.0")
    below_10 = value("chat_first_answer_text_seconds_bucket", response_source="rag", le="10.0")

    await drain(
        stream(
            (0.01, STATUS),
            (9.19, DELTA),
            (0.30, meta("rag")),
            (0.10, DONE),
            clock=clock,
        )
    )

    assert value("chat_first_answer_text_seconds_sum", response_source="rag") - first_sum == pytest.approx(9.2)
    assert value("chat_response_duration_seconds_sum", response_source="rag") - total_sum == pytest.approx(9.6)
    assert value("chat_first_answer_text_seconds_count", response_source="rag") - first_count == 1
    # 9.2 s is above the 8 s bound and inside the 10 s bound.
    assert value("chat_first_answer_text_seconds_bucket", response_source="rag", le="8.0") == below_8
    assert value("chat_first_answer_text_seconds_bucket", response_source="rag", le="10.0") - below_10 == 1


@pytest.mark.asyncio
async def test_only_the_first_delta_is_timed(clock):
    before = value("chat_first_answer_text_seconds_sum", response_source="direct")

    await drain(stream((1.0, DELTA), (4.0, DELTA), (0.5, meta("direct")), (0.5, DONE), clock=clock))

    assert value("chat_first_answer_text_seconds_sum", response_source="direct") - before == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_failed_request_is_not_observed(clock):
    before = snapshot(*LATENCY_COUNTS)

    await drain(stream((1.0, DELTA), (1.0, ErrorEvent()), clock=clock))

    assert snapshot(*LATENCY_COUNTS) == before


@pytest.mark.asyncio
async def test_cancelled_request_is_not_observed(clock):
    before = snapshot(*LATENCY_COUNTS)
    wrapper = obs.observe_chat_stream(stream((1.0, DELTA), (1.0, DELTA), clock=clock))

    await wrapper.__anext__()
    await wrapper.aclose()

    assert snapshot(*LATENCY_COUNTS) == before


@pytest.mark.asyncio
async def test_successful_empty_answer_counts_only_toward_response_duration(clock):
    before = snapshot(*LATENCY_COUNTS)

    await drain(stream((1.0, meta("rag")), (0.5, DONE), clock=clock))

    assert delta_between(before, snapshot(*LATENCY_COUNTS)) == {
        ("chat_response_duration_seconds_count", (("response_source", "rag"),)): 1.0
    }


# --- recording must not change responses (task 2.2) ------------------------


class ExplodingMetric:
    def labels(self, *args, **kwargs):
        raise RuntimeError("metric backend down")

    def inc(self, *args, **kwargs):
        raise RuntimeError("metric backend down")

    def observe(self, *args, **kwargs):
        raise RuntimeError("metric backend down")


@pytest.mark.asyncio
async def test_counter_failure_does_not_change_the_event_stream(monkeypatch):
    monkeypatch.setattr(obs, "CHAT_REQUESTS", ExplodingMetric())
    events = [STATUS, DELTA, SourcesEvent(items=[]), meta("rag"), DONE]

    forwarded = await drain(stream(*events))

    assert forwarded == events


@pytest.mark.asyncio
async def test_one_failing_metric_does_not_stop_the_others(monkeypatch):
    monkeypatch.setattr(obs, "CHAT_REQUESTS", ExplodingMetric())
    before = snapshot(*LATENCY_COUNTS)

    await drain(stream(DELTA, meta("rag"), DONE))

    assert delta_between(before, snapshot(*LATENCY_COUNTS)) == {
        ("chat_first_answer_text_seconds_count", (("response_source", "rag"),)): 1.0,
        ("chat_response_duration_seconds_count", (("response_source", "rag"),)): 1.0,
    }


@pytest.mark.asyncio
async def test_failure_while_recording_an_error_does_not_mask_the_original_exception(monkeypatch):
    monkeypatch.setattr(obs, "CHAT_REQUESTS", ExplodingMetric())

    with pytest.raises(RuntimeError, match="generator failed"):
        await drain(stream(STATUS, then=RuntimeError("generator failed")))


@pytest.mark.asyncio
async def test_clock_failure_does_not_stop_event_forwarding(monkeypatch):
    calls = {"n": 0}

    def flaky_clock():
        calls["n"] += 1
        if calls["n"] > 1:  # the start time is read once; every later read fails
            raise RuntimeError("clock down")
        return 0.0

    monkeypatch.setattr(obs, "_clock", flaky_clock)
    events = [DELTA, meta("rag"), DONE]

    assert await drain(stream(*events)) == events


@pytest.mark.asyncio
async def test_unrecognised_objects_are_forwarded_untouched():
    odd = object()

    assert await drain(stream(odd, DONE)) == [odd, DONE]


@pytest.mark.asyncio
async def test_unknown_source_value_never_becomes_a_label():
    class FakeMetadata:
        type = "metadata"
        response_source = "made-up-source"

    before = snapshot(REQUESTS)

    await drain(stream(FakeMetadata(), DONE))

    assert delta_between(before, snapshot(REQUESTS)) == {request_key("unknown", "success"): 1.0}


# --- retrieval and rerank helpers never raise (tasks 2.4, 2.5) -------------


def test_record_retrieval_counts_one_outcome_and_the_retries():
    before = snapshot("rag_retrieval_total", "rag_retrieval_retries_total")

    obs.record_retrieval(low_confidence=True, retries=2)

    assert delta_between(before, snapshot("rag_retrieval_total", "rag_retrieval_retries_total")) == {
        ("rag_retrieval_total", (("low_confidence", "true"),)): 1.0,
        ("rag_retrieval_retries_total", ()): 2.0,
    }


def test_record_retrieval_without_retries_leaves_the_retry_counter_alone():
    before = snapshot("rag_retrieval_retries_total")

    obs.record_retrieval(low_confidence=False, retries=0)

    assert snapshot("rag_retrieval_retries_total") == before


def test_record_helpers_swallow_backend_failures(monkeypatch):
    monkeypatch.setattr(obs, "RAG_RETRIEVAL", ExplodingMetric())
    monkeypatch.setattr(obs, "RAG_RETRIEVAL_RETRIES", ExplodingMetric())
    monkeypatch.setattr(obs, "RAG_RERANK_DURATION", ExplodingMetric())

    obs.record_retrieval(low_confidence=True, retries=1)
    obs.record_rerank("ok", 0.2)


def test_rerank_outcome_outside_the_fixed_set_is_recorded_as_error():
    before = snapshot("rag_rerank_duration_seconds_count")

    obs.record_rerank("surprise", 0.2)

    assert delta_between(before, snapshot("rag_rerank_duration_seconds_count")) == {
        ("rag_rerank_duration_seconds_count", (("outcome", "error"),)): 1.0
    }
