"""
Unit tests for the Prompt Cache Service.
"""
import itertools
import math
import pytest
import json
import logging
import random
import time
from collections import Counter
from redis.exceptions import ResponseError
from src.cache_service import PromptCacheService


class FakeRedis:
    """In-memory Redis modelled on real server behaviour.

    Differences from a MagicMock that matter for the cache index:
    values, set members and sorted-set members come back as bytes (the app's
    connection has no decode_responses); sorted sets order by score and break
    ties by member bytes; rank ranges accept negative indexes with Redis'
    clamping rules; ZREM, SREM and DEL with no arguments are errors; MGET of an
    empty list returns an empty list; a key of one type rejects commands of
    another with WRONGTYPE; and pipeline() queues commands until execute().

    Test helpers (not Redis commands): ``calls`` counts every command run
    (including those inside transactions), ``command_log`` lists them in
    order, ``transactions`` keeps the contents of each executed transaction,
    and ``expire_now`` / ``raw_set`` / ``has_key`` change or inspect state
    without being recorded as commands.
    """

    _PIPELINE_COMMANDS = frozenset({
        "get", "setex", "ttl", "mget", "delete", "sadd", "smembers", "srem",
        "zadd", "zrange", "zrevrange", "zrem", "zremrangebyrank", "zcard", "zscore",
    })

    def __init__(self):
        self._strings = {}
        self._ttls = {}
        self._sets = {}
        self._zsets = {}
        self.calls = Counter()
        self.command_log = []
        self.transactions = []

    # -- helpers ---------------------------------------------------------
    @staticmethod
    def _b(value):
        if isinstance(value, bytes):
            return value
        if isinstance(value, str):
            return value.encode()
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return str(value).encode()
        raise TypeError(f"Invalid input of type {type(value).__name__}")

    def _record(self, name, args):
        self.calls[name] += 1
        self.command_log.append((name, args))

    def _check(self, key, kind):
        stores = {"string": self._strings, "set": self._sets, "zset": self._zsets}
        for other_kind, store in stores.items():
            if other_kind != kind and key in store:
                raise ResponseError(
                    "WRONGTYPE Operation against a key holding the wrong kind of value"
                )

    @staticmethod
    def _wrong_args(command):
        return ResponseError(f"wrong number of arguments for '{command}' command")

    @staticmethod
    def _rank_slice(size, start, stop):
        """Redis rank-range semantics: inclusive, negative from the end, clamped."""
        if start < 0:
            start += size
        if stop < 0:
            stop += size
        if start < 0:
            start = 0
        if start > stop or start >= size:
            return slice(0, 0)
        if stop >= size:
            stop = size - 1
        return slice(start, stop + 1)

    def _ordered(self, key):
        zset = self._zsets.get(key, {})
        return sorted(zset.items(), key=lambda item: (item[1], item[0]))

    def _drop_if_empty(self, store, key):
        if key in store and not store[key]:
            del store[key]

    # -- test-only helpers ----------------------------------------------
    def expire_now(self, name):
        """Emulate a TTL expiry: the string key disappears, indexes are untouched."""
        key = self._b(name)
        self._strings.pop(key, None)
        self._ttls.pop(key, None)

    def raw_set(self, name, value, ttl=3600):
        """Plant an arbitrary string value without going through a command."""
        key = self._b(name)
        self._strings[key] = self._b(value)
        self._ttls[key] = ttl

    def has_key(self, name):
        key = self._b(name)
        return key in self._strings or key in self._sets or key in self._zsets

    def reset_recording(self):
        """Forget recorded commands (call after test setup, before the code under test)."""
        self.calls.clear()
        self.command_log.clear()
        self.transactions.clear()

    # -- strings ---------------------------------------------------------
    def get(self, name):
        self._record("GET", (name,))
        key = self._b(name)
        self._check(key, "string")
        return self._strings.get(key)

    def setex(self, name, time, value):
        self._record("SETEX", (name, time, value))
        key = self._b(name)
        self._sets.pop(key, None)
        self._zsets.pop(key, None)
        self._strings[key] = self._b(value)
        self._ttls[key] = int(time)
        return True

    def ttl(self, name):
        self._record("TTL", (name,))
        key = self._b(name)
        if not (key in self._strings or key in self._sets or key in self._zsets):
            return -2
        return self._ttls.get(key, -1)

    def mget(self, keys, *args):
        keys = list(keys) if isinstance(keys, (list, tuple)) else [keys]
        keys.extend(args)
        self._record("MGET", (tuple(keys),))
        return [self._strings.get(self._b(k)) for k in keys]

    def delete(self, *names):
        self._record("DEL", names)
        if not names:
            raise self._wrong_args("del")
        removed = 0
        for name in names:
            key = self._b(name)
            for store in (self._strings, self._sets, self._zsets):
                if store.pop(key, None) is not None:
                    removed += 1
            self._ttls.pop(key, None)
        return removed

    # -- sets ------------------------------------------------------------
    def sadd(self, name, *values):
        self._record("SADD", (name, *values))
        if not values:
            raise self._wrong_args("sadd")
        key = self._b(name)
        self._check(key, "set")
        members = self._sets.setdefault(key, set())
        before = len(members)
        members.update(self._b(v) for v in values)
        return len(members) - before

    def smembers(self, name):
        self._record("SMEMBERS", (name,))
        key = self._b(name)
        self._check(key, "set")
        return set(self._sets.get(key, set()))

    def srem(self, name, *values):
        self._record("SREM", (name, *values))
        if not values:
            raise self._wrong_args("srem")
        key = self._b(name)
        self._check(key, "set")
        members = self._sets.get(key, set())
        removed = sum(1 for v in values if self._b(v) in members)
        members.difference_update(self._b(v) for v in values)
        self._drop_if_empty(self._sets, key)
        return removed

    # -- sorted sets -----------------------------------------------------
    def zadd(self, name, mapping):
        self._record("ZADD", (name, mapping))
        if not mapping:
            raise self._wrong_args("zadd")
        key = self._b(name)
        self._check(key, "zset")
        zset = self._zsets.setdefault(key, {})
        added = 0
        for member, score in mapping.items():
            member_key = self._b(member)
            if member_key not in zset:
                added += 1
            zset[member_key] = float(score)
        return added

    def zrange(self, name, start, end, desc=False, withscores=False):
        self._record("ZRANGE", (name, start, end))
        key = self._b(name)
        self._check(key, "zset")
        ordered = self._ordered(key)
        if desc:
            ordered.reverse()
        picked = ordered[self._rank_slice(len(ordered), start, end)]
        return picked if withscores else [member for member, _ in picked]

    def zrevrange(self, name, start, end, withscores=False):
        self._record("ZREVRANGE", (name, start, end))
        key = self._b(name)
        self._check(key, "zset")
        ordered = list(reversed(self._ordered(key)))
        picked = ordered[self._rank_slice(len(ordered), start, end)]
        return picked if withscores else [member for member, _ in picked]

    def zrem(self, name, *values):
        self._record("ZREM", (name, *values))
        if not values:
            raise self._wrong_args("zrem")
        key = self._b(name)
        self._check(key, "zset")
        zset = self._zsets.get(key, {})
        removed = 0
        for value in values:
            if zset.pop(self._b(value), None) is not None:
                removed += 1
        self._drop_if_empty(self._zsets, key)
        return removed

    def zremrangebyrank(self, name, min, max):
        self._record("ZREMRANGEBYRANK", (name, min, max))
        key = self._b(name)
        self._check(key, "zset")
        ordered = self._ordered(key)
        doomed = ordered[self._rank_slice(len(ordered), min, max)]
        for member, _ in doomed:
            del self._zsets[key][member]
        self._drop_if_empty(self._zsets, key)
        return len(doomed)

    def zcard(self, name):
        self._record("ZCARD", (name,))
        key = self._b(name)
        self._check(key, "zset")
        return len(self._zsets.get(key, {}))

    def zscore(self, name, value):
        self._record("ZSCORE", (name, value))
        key = self._b(name)
        self._check(key, "zset")
        return self._zsets.get(key, {}).get(self._b(value))

    # -- transactions ----------------------------------------------------
    def pipeline(self, transaction=True):
        return _FakePipeline(self, transaction)


class _FakePipeline:
    """Queues commands and runs them in order on execute(), like MULTI/EXEC."""

    def __init__(self, client, transaction):
        self._client = client
        self._transaction = transaction
        self._queue = []

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        self.reset()

    def reset(self):
        self._queue = []

    def __getattr__(self, name):
        if name not in FakeRedis._PIPELINE_COMMANDS:
            raise AttributeError(name)

        def queue(*args, **kwargs):
            self._queue.append((name, args, kwargs))
            return self

        return queue

    def execute(self):
        queued, self._queue = self._queue, []
        if self._transaction:
            self._client.transactions.append(
                [(name.upper() if name != "delete" else "DEL", args) for name, args, _ in queued]
            )
        results = []
        first_error = None
        for name, args, kwargs in queued:
            try:
                results.append(getattr(self._client, name)(*args, **kwargs))
            except ResponseError as exc:
                results.append(exc)
                first_error = first_error or exc
        if first_error is not None:
            raise first_error
        return results


INDEX_KEY = "prompt_cache:index:zset"        # new Sorted Set index
LEGACY_INDEX_KEY = "prompt_cache:index"      # Set index from earlier releases


class FakeClock:
    """Strictly increasing clock so index order always equals write order."""

    def __init__(self, start=1_000_000.0, step=1.0):
        self.now = start
        self.step = step

    def __call__(self):
        value = self.now
        self.now += self.step
        return value


def _key_for(embedding):
    """Data key the service derives for an embedding (sha256 of packed doubles)."""
    import hashlib
    import struct

    raw = struct.pack(f"{len(embedding)}d", *embedding)
    return f"prompt_cache:{hashlib.sha256(raw).hexdigest()[:16]}"


def _index(fake_redis):
    """Index members as strings, oldest first."""
    return [m.decode() for m in fake_redis.zrange(INDEX_KEY, 0, -1)]


@pytest.fixture(autouse=True)
def unscoped_cache_namespace(monkeypatch):
    """Most tests assert the unscoped key layout; namespaced keys have their own tests."""
    from src import config as app_config

    monkeypatch.setattr(app_config, "PINECONE_INDEX_NAME", "")


@pytest.fixture
def fake_redis():
    """In-memory Redis stand-in that behaves like a real server (see FakeRedis)."""
    return FakeRedis()


@pytest.fixture
def fake_clock():
    return FakeClock()


@pytest.fixture
def cache(fake_redis, fake_clock):
    """PromptCacheService on the fake Redis with a deterministic clock."""
    return PromptCacheService(fake_redis, clock=fake_clock)


class TestFakeRedis:
    """The fake must behave like a real Redis, or the cache tests prove nothing."""

    def test_values_and_members_come_back_as_bytes(self, fake_redis):
        fake_redis.setex("k", 60, "v")
        fake_redis.sadd("s", "m")
        fake_redis.zadd("z", {"a": 1.0})

        assert fake_redis.get("k") == b"v"
        assert fake_redis.smembers("s") == {b"m"}
        assert fake_redis.zrange("z", 0, -1) == [b"a"]
        assert fake_redis.mget(["k", "missing"]) == [b"v", None]

    def test_sorted_set_orders_by_score_then_member_lexicographically(self, fake_redis):
        fake_redis.zadd("z", {"b": 1.0, "a": 1.0, "c": 0.5, "d": 2.0})

        assert fake_redis.zrange("z", 0, -1) == [b"c", b"a", b"b", b"d"]
        assert fake_redis.zrevrange("z", 0, -1) == [b"d", b"b", b"a", b"c"]

    def test_zadd_on_existing_member_updates_score_without_growing(self, fake_redis):
        assert fake_redis.zadd("z", {"a": 1.0, "b": 2.0}) == 2
        assert fake_redis.zadd("z", {"a": 3.0}) == 0

        assert fake_redis.zcard("z") == 2
        assert fake_redis.zscore("z", "a") == 3.0
        assert fake_redis.zrange("z", 0, -1) == [b"b", b"a"]

    @pytest.mark.parametrize(
        "start, stop, expected",
        [
            (0, -1, [b"a", b"b", b"c", b"d"]),
            (-2, -1, [b"c", b"d"]),
            (0, 1, [b"a", b"b"]),
            (1, 99, [b"b", b"c", b"d"]),
            (0, -5, []),    # stop resolves to -1: nothing, not "everything"
            (0, -4, [b"a"]),
            (3, 1, []),
            (9, 12, []),
        ],
    )
    def test_zrange_negative_and_out_of_range_indexes(self, fake_redis, start, stop, expected):
        fake_redis.zadd("z", {"a": 1.0, "b": 2.0, "c": 3.0, "d": 4.0})

        assert fake_redis.zrange("z", start, stop) == expected

    def test_zremrangebyrank_removes_the_range_and_counts_it(self, fake_redis):
        fake_redis.zadd("z", {"a": 1.0, "b": 2.0, "c": 3.0, "d": 4.0})

        assert fake_redis.zremrangebyrank("z", 0, -3) == 2
        assert fake_redis.zrange("z", 0, -1) == [b"c", b"d"]
        assert fake_redis.zremrangebyrank("z", 0, -5) == 0

    def test_empty_sorted_set_and_set_keys_disappear(self, fake_redis):
        fake_redis.zadd("z", {"a": 1.0})
        fake_redis.sadd("s", "m")
        fake_redis.zrem("z", "a")
        fake_redis.srem("s", "m")

        assert not fake_redis.has_key("z")
        assert not fake_redis.has_key("s")

    @pytest.mark.parametrize(
        "call",
        [
            lambda r: r.zrem("z"),
            lambda r: r.delete(),
            lambda r: r.srem("s"),
        ],
    )
    def test_commands_with_empty_arguments_raise(self, fake_redis, call):
        fake_redis.zadd("z", {"a": 1.0})
        fake_redis.sadd("s", "m")

        with pytest.raises(ResponseError, match="wrong number of arguments"):
            call(fake_redis)

    def test_mget_of_empty_list_returns_empty_list(self, fake_redis):
        assert fake_redis.mget([]) == []

    def test_delete_returns_count_of_existing_keys_across_types(self, fake_redis):
        fake_redis.setex("k", 60, "v")
        fake_redis.sadd("s", "m")
        fake_redis.zadd("z", {"a": 1.0})

        assert fake_redis.delete("k", "s", "z", "missing") == 3
        assert not any(fake_redis.has_key(k) for k in ("k", "s", "z"))

    def test_pipeline_defers_commands_and_returns_results_in_order(self, fake_redis):
        pipe = fake_redis.pipeline()
        pipe.zadd("z", {"a": 1.0})
        pipe.zrange("z", 0, -1)
        pipe.zremrangebyrank("z", 0, -1)

        assert not fake_redis.has_key("z")  # nothing ran yet
        assert pipe.execute() == [1, [b"a"], 1]
        assert not fake_redis.has_key("z")

    def test_pipeline_records_transaction_contents_in_order(self, fake_redis):
        with fake_redis.pipeline(transaction=True) as pipe:
            pipe.zadd("z", {"a": 1.0})
            pipe.zrange("z", 0, -3)
            pipe.zremrangebyrank("z", 0, -3)
            pipe.execute()

        assert fake_redis.transactions == [
            [
                ("ZADD", ("z", {"a": 1.0})),
                ("ZRANGE", ("z", 0, -3)),
                ("ZREMRANGEBYRANK", ("z", 0, -3)),
            ]
        ]

    def test_non_transactional_pipeline_is_not_recorded_as_a_transaction(self, fake_redis):
        pipe = fake_redis.pipeline(transaction=False)
        pipe.zadd("z", {"a": 1.0})
        pipe.execute()

        assert fake_redis.transactions == []

    def test_zadd_on_a_set_key_raises_wrongtype(self, fake_redis):
        fake_redis.sadd("prompt_cache:index", "x")

        with pytest.raises(ResponseError, match="WRONGTYPE"):
            fake_redis.zadd("prompt_cache:index", {"a": 1.0})

    def test_wrongtype_inside_a_transaction_surfaces_on_execute(self, fake_redis):
        fake_redis.sadd("s", "x")
        pipe = fake_redis.pipeline()
        pipe.zadd("s", {"a": 1.0})

        with pytest.raises(ResponseError, match="WRONGTYPE"):
            pipe.execute()

    def test_sadd_on_a_sorted_set_key_raises_wrongtype(self, fake_redis):
        fake_redis.zadd("z", {"a": 1.0})

        with pytest.raises(ResponseError, match="WRONGTYPE"):
            fake_redis.sadd("z", "x")

    def test_command_counts_include_commands_inside_transactions(self, fake_redis):
        fake_redis.mget(["a"])
        fake_redis.mget(["b"])
        with fake_redis.pipeline() as pipe:
            pipe.zadd("z", {"a": 1.0})
            pipe.execute()

        assert fake_redis.calls["MGET"] == 2
        assert fake_redis.calls["ZADD"] == 1
        assert fake_redis.calls["GET"] == 0

    def test_expire_now_makes_a_key_vanish_without_touching_indexes(self, fake_redis):
        fake_redis.setex("k", 60, "v")
        fake_redis.zadd("z", {"k": 1.0})

        fake_redis.expire_now("k")

        assert fake_redis.get("k") is None
        assert fake_redis.ttl("k") == -2
        assert fake_redis.zrange("z", 0, -1) == [b"k"]

    def test_ttl_reports_the_stored_ttl(self, fake_redis):
        fake_redis.setex("k", 3600, "v")

        assert fake_redis.ttl("k") == 3600
        assert fake_redis.ttl("missing") == -2


class TestCacheMaxEntriesSetting:
    """CACHE_MAX_ENTRIES must be an integer >= 1; anything else falls back to 50."""

    @pytest.mark.parametrize(
        "raw, expected, warns",
        [
            (None, 50, False),
            ("10", 10, False),
            ("1", 1, False),
            ("0", 50, True),
            ("-3", 50, True),
            ("abc", 50, True),
            ("", 50, True),
            ("10.5", 50, True),
        ],
    )
    def test_parse_cache_max_entries(self, raw, expected, warns, caplog):
        from src.config import parse_cache_max_entries

        with caplog.at_level(logging.WARNING, logger="config"):
            assert parse_cache_max_entries(raw) == expected

        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert bool(warnings) is warns
        if warns:
            assert "CACHE_MAX_ENTRIES" in warnings[0].getMessage()
            assert repr(raw) in warnings[0].getMessage()

    def test_module_level_setting_is_a_valid_integer(self):
        from src import config

        assert isinstance(config.CACHE_MAX_ENTRIES, int)
        assert config.CACHE_MAX_ENTRIES >= 1


class TestCacheSimilarity:
    """Tests for similarity calculation."""
    
    def test_identical_embeddings(self, cache):
        """Test that identical embeddings have similarity of 1.0."""
        embedding = [0.1, 0.2, 0.3, 0.4, 0.5]
        similarity = cache.calculate_similarity(embedding, embedding)
        assert abs(similarity - 1.0) < 0.0001
    
    def test_orthogonal_embeddings(self, cache):
        """Test that orthogonal embeddings have similarity of 0.0."""
        embedding1 = [1.0, 0.0, 0.0]
        embedding2 = [0.0, 1.0, 0.0]
        similarity = cache.calculate_similarity(embedding1, embedding2)
        assert abs(similarity - 0.0) < 0.0001
    
    def test_similar_embeddings(self, cache):
        """Test that similar embeddings have high similarity."""
        embedding1 = [0.9, 0.1, 0.0]
        embedding2 = [0.85, 0.15, 0.0]
        similarity = cache.calculate_similarity(embedding1, embedding2)
        assert similarity > 0.9
    
    def test_zero_vector_handling(self, cache):
        """Test handling of zero vectors."""
        embedding1 = [0.0, 0.0, 0.0]
        embedding2 = [1.0, 2.0, 3.0]
        similarity = cache.calculate_similarity(embedding1, embedding2)
        assert similarity == 0.0


def _entry(embedding, question="Q", answer="A", sources=None, hit_count=0):
    """A well-formed stored cache value."""
    return {
        "question": question,
        "answer": answer,
        "sources": [] if sources is None else sources,
        "embedding": embedding,
        "timestamp": int(time.time()),
        "hit_count": hit_count,
    }


def _seed(fake_redis, key, data, score=1.0, ttl=3600):
    """Plant a raw value and register it in the index, bypassing the service."""
    value = data if isinstance(data, (bytes, str)) else json.dumps(data)
    fake_redis.raw_set(key, value, ttl=ttl)
    fake_redis.zadd(INDEX_KEY, {key: score})


def _at_similarity(similarity):
    """Embedding whose cosine similarity to [1.0, 0.0] is `similarity`."""
    return [similarity, math.sqrt(1 - similarity ** 2)]


class TestCacheRetrieval:
    """Tests for cache retrieval operations."""

    def test_empty_cache_returns_none_without_reading_values(self, cache, fake_redis):
        """Querying an empty index returns None and never issues a value read."""
        assert cache.get_cached_response([0.1, 0.2, 0.3]) is None

        assert fake_redis.calls["MGET"] == 0
        assert fake_redis.calls["GET"] == 0

    def test_cache_hit_with_high_similarity(self, cache, fake_redis):
        """Test successful cache hit with similar question."""
        key = "prompt_cache:12345"
        _seed(fake_redis, key, _entry(
            [0.85, 0.15, 0.0],
            question="What is carbon management?",
            answer="Carbon management is...",
            sources=["What is carbon management?"],
            hit_count=2,
        ))

        result = cache.get_cached_response([0.9, 0.1, 0.0], threshold=0.85)

        assert result is not None
        assert result["question"] == "What is carbon management?"
        assert result["answer"] == "Carbon management is..."
        assert result["sources"] == ["What is carbon management?"]
        assert result["similarity"] > 0.85
        assert result["hit_count"] == 2

    def test_hit_increments_the_stored_hit_count_and_keeps_the_ttl(self, cache, fake_redis):
        key = "prompt_cache:12345"
        _seed(fake_redis, key, _entry([1.0, 0.0], hit_count=2), ttl=1800)

        cache.get_cached_response([1.0, 0.0])

        assert json.loads(fake_redis.get(key))["hit_count"] == 3
        assert fake_redis.ttl(key) == 1800

    def test_result_shape_is_unchanged(self, cache, fake_redis):
        _seed(fake_redis, "prompt_cache:12345", _entry([1.0, 0.0], sources=["s"]))

        result = cache.get_cached_response([1.0, 0.0])

        assert set(result) == {"question", "answer", "sources", "similarity", "hit_count"}

    def test_cache_miss_with_low_similarity(self, cache, fake_redis):
        """Orthogonal embedding is compared and rejected, not skipped."""
        _seed(fake_redis, "prompt_cache:12345", _entry([0.0, 1.0], question="Different question"))

        assert cache.get_cached_response([1.0, 0.0], threshold=0.85) is None
        # the same entry is returned once the threshold admits it: it was really compared
        assert cache.get_cached_response([1.0, 0.0], threshold=-0.5)["question"] == "Different question"

    @pytest.mark.parametrize("entries, position", [(3, 3), (20, 20), (50, 1)])
    def test_position_in_the_index_does_not_matter(self, fake_redis, fake_clock, entries, position):
        service = PromptCacheService(fake_redis, clock=fake_clock, max_entries=50)
        for i in range(entries):
            _write(service, i)
        target = entries - position  # position 1 is the newest write

        result = service.get_cached_response(_vec(target))

        assert result is not None
        assert result["question"] == f"q{target}"

    def test_every_entry_is_found_whatever_the_insertion_order(self, fake_redis, fake_clock):
        service = PromptCacheService(fake_redis, clock=fake_clock, max_entries=50)
        order = list(range(20))
        random.Random(11).shuffle(order)
        for i in order:
            _write(service, i)

        for i in range(20):
            result = service.get_cached_response(_vec(i))
            assert result is not None and result["question"] == f"q{i}"

    @pytest.mark.parametrize("order", list(itertools.permutations([0.96, 0.99, 0.97])))
    def test_highest_similarity_wins_across_all_entries(self, fake_redis, fake_clock, order):
        service = PromptCacheService(fake_redis, clock=fake_clock, max_entries=50)
        for similarity in order:
            service.set_cached_response(_at_similarity(similarity), f"q{similarity}", f"a{similarity}", [])

        result = service.get_cached_response([1.0, 0.0])

        assert result["answer"] == "a0.99"
        assert result["similarity"] == pytest.approx(0.99)

    def test_similarity_equal_to_the_threshold_is_a_miss(self, cache, fake_redis):
        candidate = _at_similarity(0.97)
        _seed(fake_redis, "prompt_cache:12345", _entry(candidate))
        similarity = cache.calculate_similarity([1.0, 0.0], candidate)

        assert cache.get_cached_response([1.0, 0.0], threshold=similarity) is None
        assert cache.get_cached_response([1.0, 0.0], threshold=similarity - 1e-9) is not None

    def test_lookup_reads_every_value_with_a_single_mget(self, fake_redis, fake_clock):
        service = PromptCacheService(fake_redis, clock=fake_clock, max_entries=50)
        for i in range(20):
            _write(service, i)
        fake_redis.reset_recording()

        service.get_cached_response(_vec(0))  # the oldest entry

        assert fake_redis.calls["MGET"] == 1
        (mget_args,) = [args for name, args in fake_redis.command_log if name == "MGET"]
        newest_first = [_key_for(_vec(i)).encode() for i in reversed(range(20))]
        assert list(mget_args[0]) == newest_first

    def test_a_miss_reads_values_only_through_mget(self, fake_redis, fake_clock):
        service = PromptCacheService(fake_redis, clock=fake_clock, max_entries=50)
        for i in range(5):
            _write(service, i)
        fake_redis.reset_recording()

        assert service.get_cached_response(_vec(40)) is None

        assert fake_redis.calls["MGET"] == 1
        assert fake_redis.calls["GET"] == 0

    def test_lowered_maximum_limits_lookup_to_the_newest_entries(self, fake_redis, fake_clock):
        writer = PromptCacheService(fake_redis, clock=fake_clock, max_entries=5)
        for i in range(5):
            _write(writer, i)
        reader = PromptCacheService(fake_redis, clock=fake_clock, max_entries=3)
        fake_redis.reset_recording()

        assert reader.get_cached_response(_vec(0)) is None  # outside the newest 3
        assert reader.get_cached_response(_vec(1)) is None
        assert reader.get_cached_response(_vec(2))["question"] == "q2"
        assert reader.get_cached_response(_vec(4))["question"] == "q4"
        (first_mget, *_) = [args for name, args in fake_redis.command_log if name == "MGET"]
        assert len(first_mget[0]) == 3

    def test_redis_error_during_lookup_returns_none(self, cache, fake_redis, monkeypatch):
        def boom(*args, **kwargs):
            raise Exception("Redis error")

        monkeypatch.setattr(fake_redis, "zrevrange", boom)

        assert cache.get_cached_response([1.0, 0.0]) is None


_DROP = object()  # marks a field to delete from a stored value


def _mutated(**changes):
    """A well-formed value that would match [1.0, 0.0] exactly, with fields changed."""
    data = _entry([1.0, 0.0], question="bad q", answer="bad a", sources=["s"])
    for field, value in changes.items():
        if value is _DROP:
            del data[field]
        else:
            data[field] = value
    return data


# (id, stored value, whether the member must be removed from the index)
UNUSABLE_ENTRIES = [
    ("not-json", b"not json{", True),
    ("json-array", b"[1, 2, 3]", True),
    ("json-null", b"null", True),
    ("json-number", b"42", True),
    ("invalid-utf8", b"\xc3\x28", True),
    ("no-sources", _mutated(sources=_DROP), True),
    ("sources-not-a-list", _mutated(sources="abc"), True),
    ("sources-with-non-strings", _mutated(sources=[1, 2]), True),
    ("answer-null", _mutated(answer=None), True),
    ("answer-number", _mutated(answer=123), True),
    ("answer-empty", _mutated(answer=""), True),
    ("no-embedding", _mutated(embedding=_DROP), False),
    ("embedding-empty", _mutated(embedding=[]), False),
    ("embedding-not-a-list", _mutated(embedding=5), False),
    ("embedding-not-numbers", _mutated(embedding=["a", "b"]), False),
    ("embedding-dimension-mismatch", _mutated(embedding=[1.0, 0.0, 0.0]), False),
]


class TestExpiredAndCorruptedEntries:
    """One unusable entry must never disable lookup; expired members are purged."""

    def test_one_lookup_removes_every_expired_member(self, fake_redis, fake_clock):
        service = PromptCacheService(fake_redis, clock=fake_clock, max_entries=50)
        for i in range(6):
            _write(service, i)
        for i in range(5):  # entries 0-4 expire, entry 5 stays
            fake_redis.expire_now(_key_for(_vec(i)))
        fake_redis.reset_recording()

        result = service.get_cached_response(_vec(5))

        assert result["question"] == "q5"
        assert _index(fake_redis) == [_key_for(_vec(5))]
        assert fake_redis.calls["ZREM"] == 1  # one command for all five

    @pytest.mark.parametrize("corrupt_is_newest", [True, False])
    @pytest.mark.parametrize(
        "value, purged",
        [(value, purged) for _, value, purged in UNUSABLE_ENTRIES],
        ids=[case_id for case_id, _, _ in UNUSABLE_ENTRIES],
    )
    def test_an_unusable_entry_does_not_block_a_valid_one(
        self, cache, fake_redis, value, purged, corrupt_is_newest
    ):
        cache.set_cached_response(_at_similarity(0.97), "valid q", "valid a", ["s"])
        valid_key = _key_for(_at_similarity(0.97))
        bad_key = "prompt_cache:corrupt"
        # The bad entry is the better match (similarity 1.0) wherever it sits,
        # so it would be returned if it were not rejected.
        _seed(fake_redis, bad_key, value, score=2_000_000.0 if corrupt_is_newest else 1.0)

        result = cache.get_cached_response([1.0, 0.0])

        assert result is not None
        assert result["answer"] == "valid a"
        members = _index(fake_redis)
        assert valid_key in members
        assert (bad_key not in members) is purged

    def test_unusable_members_are_removed_with_a_single_zrem(self, cache, fake_redis):
        cache.set_cached_response(_at_similarity(0.97), "valid q", "valid a", ["s"])
        _seed(fake_redis, "prompt_cache:bad1", b"not json{", score=5.0)
        _seed(fake_redis, "prompt_cache:bad2", _mutated(sources=_DROP), score=6.0)
        fake_redis.zadd(INDEX_KEY, {"prompt_cache:gone": 7.0})  # expired: no data key
        fake_redis.reset_recording()

        cache.get_cached_response([1.0, 0.0])

        assert fake_redis.calls["ZREM"] == 1
        assert _index(fake_redis) == [_key_for(_at_similarity(0.97))]

    def test_nothing_to_remove_means_no_zrem(self, cache, fake_redis):
        cache.set_cached_response(_at_similarity(0.97), "valid q", "valid a", ["s"])
        _seed(fake_redis, "prompt_cache:no-embedding", _mutated(embedding=_DROP), score=5.0)
        fake_redis.reset_recording()

        cache.get_cached_response([1.0, 0.0])

        assert fake_redis.calls["ZREM"] == 0

    def test_entry_without_sources_is_unusable_even_when_it_matches(self, cache, fake_redis):
        _seed(fake_redis, "prompt_cache:no-sources", _mutated(sources=_DROP))

        assert cache.get_cached_response([1.0, 0.0]) is None
        assert _index(fake_redis) == []

    def test_a_valid_entry_below_the_threshold_stays_in_the_index(self, cache, fake_redis):
        _seed(fake_redis, "prompt_cache:other", _entry([0.0, 1.0], sources=["s"]))

        assert cache.get_cached_response([1.0, 0.0]) is None
        assert _index(fake_redis) == ["prompt_cache:other"]


class TestCacheStorage:
    """Tests for cache storage operations."""
    
    def test_set_cached_response(self, cache, fake_redis, fake_clock):
        """Test storing a new cached response."""
        embedding = [0.1, 0.2, 0.3, 0.4, 0.5]
        question = "What is the system?"
        answer = "The system is a carbon management tool."
        sources = ["What is the system?"]
        first_tick = fake_clock.now

        result = cache.set_cached_response(embedding, question, answer, sources, ttl=3600)

        assert result is True
        key = _key_for(embedding)
        assert fake_redis.ttl(key) == 3600
        stored_data = json.loads(fake_redis.get(key))
        assert stored_data["question"] == question
        assert stored_data["answer"] == answer
        assert stored_data["sources"] == sources
        assert stored_data["embedding"] == embedding
        assert stored_data["hit_count"] == 0

        # Registered in the Sorted Set index with the write time as a float score
        assert _index(fake_redis) == [key]
        assert fake_redis.zscore(INDEX_KEY, key) == first_tick
        assert isinstance(fake_redis.zscore(INDEX_KEY, key), float)

    def test_default_clock_scores_entries_with_wall_time(self, fake_redis):
        service = PromptCacheService(fake_redis)
        before = time.time()
        service.set_cached_response([0.1, 0.2], "q", "a", [], ttl=3600)
        after = time.time()

        score = fake_redis.zscore(INDEX_KEY, _key_for([0.1, 0.2]))
        assert before <= score <= after

    def test_constructor_stays_compatible_with_positional_use(self, fake_redis):
        """Existing callers pass only the connection; the new arguments are optional."""
        assert PromptCacheService(fake_redis).set_cached_response([0.1, 0.2], "q", "a", []) is True
        service = PromptCacheService(fake_redis, FakeClock(), 3)
        assert service.set_cached_response([0.3, 0.4], "q", "a", []) is True

    @pytest.mark.parametrize("bad_limit", [0, -1, True, "5", 2.5])
    def test_constructor_rejects_an_invalid_max_entries(self, fake_redis, bad_limit):
        with pytest.raises(ValueError):
            PromptCacheService(fake_redis, max_entries=bad_limit)

    def test_write_succeeds_when_legacy_set_index_exists(self, cache, fake_redis):
        fake_redis.sadd(LEGACY_INDEX_KEY, "prompt_cache:old1", "prompt_cache:old2")
        fake_redis.command_log.clear()

        assert cache.set_cached_response([0.1, 0.2], "q", "a", []) is True

        assert fake_redis.smembers(LEGACY_INDEX_KEY) == {b"prompt_cache:old1", b"prompt_cache:old2"}
        touched = [name for name, args in fake_redis.command_log if args and args[0] == LEGACY_INDEX_KEY]
        assert touched == ["SMEMBERS"]  # only the assertion above, none from the service

    def test_set_cached_response_roundtrip_sources(self, cache, fake_redis):
        """Sources written by set_cached_response can be read back by get_cached_response."""
        embedding = [0.9, 0.1, 0.0]
        sources = ["密碼忘記了要怎麼重設啊？"]

        cache.set_cached_response(embedding, "問題", "答案", sources, ttl=3600)
        result = cache.get_cached_response(embedding, threshold=0.5)

        assert result is not None
        assert result["sources"] == sources

    def test_set_cached_response_with_error(self, cache, fake_redis, monkeypatch):
        """Test error handling when storing fails."""
        def boom(*args, **kwargs):
            raise Exception("Redis error")

        monkeypatch.setattr(fake_redis, "setex", boom)

        result = cache.set_cached_response([0.1, 0.2, 0.3], "Question", "Answer", [])

        assert result is False


def _vec(i, dim=64):
    """One-hot embedding: distinct per i, orthogonal to every other i."""
    v = [0.0] * dim
    v[i] = 1.0
    return v


def _write(service, i, answer=None):
    return service.set_cached_response(_vec(i), f"q{i}", answer or f"a{i}", [f"s{i}"], ttl=3600)


class TestCacheEviction:
    """The index is capped; writes evict the oldest entries and their data keys."""

    def test_writing_beyond_the_maximum_evicts_the_oldest_entry(self, fake_redis, fake_clock):
        service = PromptCacheService(fake_redis, clock=fake_clock, max_entries=3)
        for i in range(4):  # A, B, C, D
            assert _write(service, i) is True

        assert _index(fake_redis) == [_key_for(_vec(i)) for i in (1, 2, 3)]
        assert not fake_redis.has_key(_key_for(_vec(0)))
        assert all(fake_redis.has_key(_key_for(_vec(i))) for i in (1, 2, 3))

    def test_eviction_is_one_transaction_and_deletes_exactly_what_it_removed(self, fake_redis, fake_clock):
        service = PromptCacheService(fake_redis, clock=fake_clock, max_entries=3)
        for i in range(3):
            _write(service, i)
        fake_redis.transactions.clear()
        fake_redis.command_log.clear()

        _write(service, 3)

        evicted_key = _key_for(_vec(0))
        assert len(fake_redis.transactions) == 1
        transaction = fake_redis.transactions[0]
        assert [name for name, _ in transaction] == ["ZADD", "ZRANGE", "ZREMRANGEBYRANK"]
        assert transaction[0][1] == (INDEX_KEY, {_key_for(_vec(3)): fake_clock.now - 1})
        # everything except the newest 3 members: ranks 0 .. -(3 + 1)
        assert transaction[1][1] == (INDEX_KEY, 0, -4)
        assert transaction[2][1] == (INDEX_KEY, 0, -4)

        # the data keys deleted afterwards are exactly the members the transaction removed
        deletes = [args for name, args in fake_redis.command_log if name == "DEL"]
        assert [set(map(bytes.decode, args)) for args in deletes] == [{evicted_key}]
        names = [name for name, _ in fake_redis.command_log]
        assert names.index("DEL") > names.index("ZREMRANGEBYRANK")

    def test_a_maximum_of_one_keeps_only_the_newest(self, fake_redis, fake_clock):
        service = PromptCacheService(fake_redis, clock=fake_clock, max_entries=1)
        for i in range(3):
            _write(service, i)

        assert _index(fake_redis) == [_key_for(_vec(2))]
        assert not fake_redis.has_key(_key_for(_vec(0)))
        assert not fake_redis.has_key(_key_for(_vec(1)))

    def test_lowered_maximum_shrinks_the_index_on_the_next_write(self, fake_redis, fake_clock):
        writer = PromptCacheService(fake_redis, clock=fake_clock, max_entries=5)
        for i in range(5):
            _write(writer, i)
        assert fake_redis.zcard(INDEX_KEY) == 5

        _write(PromptCacheService(fake_redis, clock=fake_clock, max_entries=3), 5)

        assert _index(fake_redis) == [_key_for(_vec(i)) for i in (3, 4, 5)]
        assert not any(fake_redis.has_key(_key_for(_vec(i))) for i in (0, 1, 2))

    def test_no_eviction_commands_with_empty_arguments_below_the_limit(self, fake_redis, fake_clock):
        service = PromptCacheService(fake_redis, clock=fake_clock, max_entries=5)

        results = [_write(service, i) for i in range(3)]

        assert results == [True, True, True]  # the fake raises on DEL/ZREM with no arguments
        assert fake_redis.calls["DEL"] == 0
        assert fake_redis.calls["ZREM"] == 0

    def test_rewriting_a_key_in_a_full_index_evicts_nothing(self, fake_redis, fake_clock):
        service = PromptCacheService(fake_redis, clock=fake_clock, max_entries=3)
        for i in range(3):
            _write(service, i)

        _write(service, 0, answer="refreshed")

        assert _index(fake_redis) == [_key_for(_vec(i)) for i in (1, 2, 0)]
        assert all(fake_redis.has_key(_key_for(_vec(i))) for i in range(3))
        assert fake_redis.calls["DEL"] == 0

    def test_index_never_exceeds_an_explicit_maximum(self, fake_redis, fake_clock):
        service = PromptCacheService(fake_redis, clock=fake_clock, max_entries=10)
        for i in range(15):
            _write(service, i)
            assert fake_redis.zcard(INDEX_KEY) <= 10

        assert fake_redis.zcard(INDEX_KEY) == 10

    def test_maximum_is_read_from_the_setting_at_call_time(self, fake_redis, fake_clock, monkeypatch):
        service = PromptCacheService(fake_redis, clock=fake_clock)  # no explicit cap
        monkeypatch.setattr("src.config.CACHE_MAX_ENTRIES", 2)

        for i in range(4):
            _write(service, i)

        assert _index(fake_redis) == [_key_for(_vec(i)) for i in (2, 3)]

    def test_a_failing_transaction_makes_the_write_report_failure(self, cache, fake_redis, monkeypatch):
        def boom(*args, **kwargs):
            raise Exception("Redis error")

        monkeypatch.setattr(fake_redis, "pipeline", boom)

        assert cache.set_cached_response([0.1, 0.2], "q", "a", []) is False


class TestLegacyIndexCoexistence:
    """A Set index from an earlier release may still be in Redis after deployment."""

    def test_write_and_lookup_work_and_leave_the_legacy_set_untouched(self, cache, fake_redis):
        legacy = {b"prompt_cache:old1", b"prompt_cache:old2"}
        fake_redis.sadd(LEGACY_INDEX_KEY, *legacy)
        fake_redis.raw_set("prompt_cache:old1", json.dumps(_entry([1.0, 0.0], answer="old answer", sources=["s"])))
        fake_redis.reset_recording()

        assert cache.set_cached_response([0.0, 1.0], "new q", "new a", ["s"]) is True
        result = cache.get_cached_response([0.0, 1.0])

        assert result["answer"] == "new a"
        assert cache.get_cached_response([1.0, 0.0]) is None  # the old entry is not indexed any more
        touched = [name for name, args in fake_redis.command_log if args and args[0] == LEGACY_INDEX_KEY]
        assert touched == []
        assert fake_redis.smembers(LEGACY_INDEX_KEY) == legacy
        assert fake_redis.has_key("prompt_cache:old1")


class TestCacheManagement:
    """Tests for cache management operations."""

    def test_clear_cache_removes_both_indexes_and_counts_shared_keys_once(self, cache, fake_redis):
        for key in ("a", "b", "c"):
            fake_redis.raw_set(f"prompt_cache:{key}", json.dumps(_entry([1.0, 0.0], sources=[])))
        fake_redis.zadd(INDEX_KEY, {"prompt_cache:a": 1.0, "prompt_cache:b": 2.0})
        fake_redis.sadd(LEGACY_INDEX_KEY, "prompt_cache:b", "prompt_cache:c")  # b is in both

        count = cache.clear_cache()

        assert count == 3
        assert not any(
            fake_redis.has_key(k)
            for k in ("prompt_cache:a", "prompt_cache:b", "prompt_cache:c", INDEX_KEY, LEGACY_INDEX_KEY)
        )

    def test_clear_cache_with_only_a_legacy_index(self, cache, fake_redis):
        fake_redis.raw_set("prompt_cache:old", json.dumps(_entry([1.0, 0.0], sources=[])))
        fake_redis.sadd(LEGACY_INDEX_KEY, "prompt_cache:old")

        assert cache.clear_cache() == 1
        assert not fake_redis.has_key("prompt_cache:old")
        assert not fake_redis.has_key(LEGACY_INDEX_KEY)

    def test_clear_cache_on_an_empty_cache_returns_zero(self, cache, fake_redis):
        assert cache.clear_cache() == 0
        assert fake_redis.calls["DEL"] == 1  # only the two index keys, never an empty DEL

    def test_clear_cache_deletes_a_large_legacy_index_in_batches(self, cache, fake_redis):
        keys = [f"prompt_cache:old{i}" for i in range(1200)]
        for key in keys:
            fake_redis.raw_set(key, "{}")
        fake_redis.sadd(LEGACY_INDEX_KEY, *keys)
        fake_redis.reset_recording()

        assert cache.clear_cache() == 1200
        assert not any(fake_redis.has_key(k) for k in keys)
        assert max(len(args) for name, args in fake_redis.command_log if name == "DEL") <= 500

    def test_clear_cache_raises_on_redis_error(self, cache, fake_redis, monkeypatch):
        # A failure must be distinguishable from an empty cache (returns 0).
        def boom(*args, **kwargs):
            raise ConnectionError("Redis error")

        monkeypatch.setattr(fake_redis, "zrange", boom)

        with pytest.raises(ConnectionError):
            cache.clear_cache()

    def test_get_cache_stats(self, cache, fake_redis):
        """Test retrieving cache statistics."""
        _seed(fake_redis, "prompt_cache:123", _entry([0.1, 0.2], question="Q1", answer="A1", hit_count=5))
        _seed(fake_redis, "prompt_cache:456", _entry([0.3, 0.4], question="Q2", answer="A2", hit_count=3))

        stats = cache.get_cache_stats()

        assert stats["total_entries"] == 2
        assert stats["total_hits"] == 8
        assert stats["avg_hits_per_entry"] == 4.0

    def test_stats_only_reflect_the_new_index(self, cache, fake_redis):
        _seed(fake_redis, "prompt_cache:123", _entry([0.1, 0.2], hit_count=5))
        fake_redis.raw_set("prompt_cache:old", json.dumps(_entry([0.1, 0.2], hit_count=100)))
        fake_redis.sadd(LEGACY_INDEX_KEY, "prompt_cache:old")

        stats = cache.get_cache_stats()

        assert stats["total_entries"] == 1
        assert stats["total_hits"] == 5

    def test_stats_tolerate_expired_and_corrupted_entries(self, cache, fake_redis):
        _seed(fake_redis, "prompt_cache:good", _entry([0.1, 0.2], hit_count=4))
        _seed(fake_redis, "prompt_cache:bad", b"not json{", score=2.0)
        fake_redis.zadd(INDEX_KEY, {"prompt_cache:gone": 3.0})

        stats = cache.get_cache_stats()

        assert stats == {"total_entries": 3, "total_hits": 4, "avg_hits_per_entry": 4 / 3}

    def test_stats_of_an_empty_cache(self, cache, fake_redis):
        assert cache.get_cache_stats() == {"total_entries": 0, "total_hits": 0, "avg_hits_per_entry": 0}
        assert fake_redis.calls["MGET"] == 0

    def test_stats_report_a_redis_error(self, cache, fake_redis, monkeypatch):
        def boom(*args, **kwargs):
            raise Exception("Redis error")

        monkeypatch.setattr(fake_redis, "zrange", boom)

        assert "error" in cache.get_cache_stats()


class TestCacheKeyStability:
    """Tests for cache key stability and collision resistance."""

    def test_service_key_matches_the_embedding_hash(self, cache, fake_redis):
        """The key the service writes is the sha256 prefix of the packed embedding."""
        embedding = [0.1 * i for i in range(1536)]

        cache.set_cached_response(embedding, "q", "a", [], ttl=3600)

        assert _index(fake_redis) == [_key_for(embedding)]

    def test_cache_key_no_collision_on_partial_match(self, cache):
        """Same first 10 dims but different later dims must produce different keys."""
        import hashlib, struct, random
        random.seed(42)
        shared_prefix = [round(random.uniform(0, 1), 4) for _ in range(10)]

        keys = set()
        for _ in range(1000):
            suffix = [round(random.uniform(0, 1), 4) for _ in range(1526)]
            embedding = shared_prefix + suffix
            raw = struct.pack(f"{len(embedding)}d", *embedding)
            key = hashlib.sha256(raw).hexdigest()[:16]
            keys.add(key)

        assert len(keys) == 1000, f"Expected 1000 unique keys, got {len(keys)}"

    def test_cache_key_stability_same_embedding(self, cache):
        """Same embedding produces identical key every time."""
        import hashlib, struct
        embedding = [0.1 * i for i in range(1536)]
        raw = struct.pack(f"{len(embedding)}d", *embedding)
        expected = hashlib.sha256(raw).hexdigest()[:16]

        for _ in range(10):
            computed = hashlib.sha256(raw).hexdigest()[:16]
            assert computed == expected


class TestCacheDeduplication:
    """Tests for cache entry deduplication / hit_count preservation."""

    def test_duplicate_cache_preserves_hit_count(self, cache, fake_redis):
        """Caching the same question twice must keep the existing hit_count."""
        embedding = [0.1, 0.2, 0.3, 0.4, 0.5]
        existing_data = {
            "question": "Same question",
            "answer": "Old answer",
            "sources": [],
            "embedding": embedding,
            "timestamp": 1000,
            "hit_count": 5,
        }
        fake_redis.raw_set(_key_for(embedding), json.dumps(existing_data))

        result = cache.set_cached_response(embedding, "Same question", "New answer", [], ttl=3600)

        assert result is True
        stored = json.loads(fake_redis.get(_key_for(embedding)))
        assert stored["hit_count"] == 5, "hit_count must be preserved from existing entry"
        assert stored["answer"] == "New answer"

    def test_rewriting_a_key_does_not_grow_the_index_and_makes_it_newest(self, cache, fake_redis):
        first, second = [0.1, 0.2, 0.3], [0.4, 0.5, 0.6]
        cache.set_cached_response(first, "q1", "a1", [], ttl=3600)
        cache.set_cached_response(second, "q2", "a2", [], ttl=3600)
        assert _index(fake_redis) == [_key_for(first), _key_for(second)]

        cache.set_cached_response(first, "q1", "a1 again", [], ttl=3600)

        assert _index(fake_redis) == [_key_for(second), _key_for(first)]
        assert fake_redis.zcard(INDEX_KEY) == 2

    @pytest.mark.parametrize(
        "existing",
        [
            b"not json{",
            b"[1, 2, 3]",
            b"\xff\xfe not utf-8",
            b'{"hit_count": "many"}',
        ],
    )
    def test_corrupted_existing_value_does_not_block_a_rewrite(self, cache, fake_redis, existing):
        embedding = [0.1, 0.2, 0.3]
        fake_redis.raw_set(_key_for(embedding), existing)

        result = cache.set_cached_response(embedding, "q", "a", ["s"], ttl=3600)

        assert result is True
        stored = json.loads(fake_redis.get(_key_for(embedding)))
        assert stored["hit_count"] == 0
        assert stored["answer"] == "a"
        assert _index(fake_redis) == [_key_for(embedding)]


class TestEdgeCases:
    """Tests for edge cases and error handling."""

    def test_expired_cache_entries_removed_from_index(self, cache, fake_redis):
        """Test that expired entries are removed from the index during lookup."""
        key = "prompt_cache:expired"
        fake_redis.zadd(INDEX_KEY, {key: 1.0})  # indexed, but the data key is gone
        fake_redis.reset_recording()

        result = cache.get_cached_response([0.1, 0.2, 0.3])

        assert result is None
        assert fake_redis.zcard(INDEX_KEY) == 0
        assert fake_redis.calls["ZREM"] == 1

    def test_cache_with_missing_embedding_field(self, cache, fake_redis):
        """An entry without an embedding is skipped, not fatal."""
        data = _entry([0.1, 0.2, 0.3])
        del data["embedding"]
        _seed(fake_redis, "prompt_cache:123", data)

        assert cache.get_cached_response([0.1, 0.2, 0.3], threshold=0.85) is None


class TestCacheNamespace:
    """Spec: cache-namespace — entries are scoped by the Pinecone index name."""

    def test_default_namespace_follows_the_index_name(self, fake_redis, fake_clock, monkeypatch):
        from src import config as app_config

        monkeypatch.setattr(app_config, "PINECONE_INDEX_NAME", "carbon-assistant-sample-index")
        service = PromptCacheService(fake_redis, clock=fake_clock)

        assert service.set_cached_response([1.0, 0.0], "q", "a", ["s"]) is True
        expected = _key_for([1.0, 0.0]).replace("prompt_cache:", "prompt_cache:carbon-assistant-sample-index:")
        assert fake_redis.has_key(expected)
        index_key = "prompt_cache:carbon-assistant-sample-index:index:zset"
        assert [m.decode() for m in fake_redis.zrange(index_key, 0, -1)] == [expected]
        assert not fake_redis.has_key(INDEX_KEY)

    def test_namespaces_do_not_share_entries(self, fake_redis, fake_clock):
        a = PromptCacheService(fake_redis, clock=fake_clock, namespace="index-a")
        b = PromptCacheService(fake_redis, clock=fake_clock, namespace="index-b")

        a.set_cached_response([1.0, 0.0], "q", "answer from a", ["s"])

        assert b.get_cached_response([1.0, 0.0]) is None
        assert a.get_cached_response([1.0, 0.0])["answer"] == "answer from a"

    def test_clearing_one_namespace_keeps_the_other(self, fake_redis, fake_clock):
        a = PromptCacheService(fake_redis, clock=fake_clock, namespace="index-a")
        b = PromptCacheService(fake_redis, clock=fake_clock, namespace="index-b")
        a.set_cached_response([1.0, 0.0], "q", "from a", ["s"])
        b.set_cached_response([1.0, 0.0], "q", "from b", ["s"])

        assert a.clear_cache() == 1

        assert a.get_cached_response([1.0, 0.0]) is None
        assert b.get_cached_response([1.0, 0.0])["answer"] == "from b"

    def test_empty_namespace_uses_the_unscoped_layout(self, fake_redis, fake_clock):
        service = PromptCacheService(fake_redis, clock=fake_clock, namespace="")
        service.set_cached_response([1.0, 0.0], "q", "a", ["s"])

        assert fake_redis.has_key(_key_for([1.0, 0.0]))
        assert _index(fake_redis) == [_key_for([1.0, 0.0])]
