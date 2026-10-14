#!/usr/bin/env python3
"""
Measure what one semantic-cache lookup costs on the web process, without Redis.

A lookup reads every indexed value with one MGET, then parses each JSON value,
converts its 1536-dim embedding to a numpy array and computes a cosine
similarity. Network transfer is not measured here; only the work done in Python
after the bytes arrive. The lookup runs synchronously inside an async request,
so this time is also how long the event loop is blocked.

Usage:
    python scripts/benchmark_cache_lookup.py
    python scripts/benchmark_cache_lookup.py --entries 50 100 200 --repeat 50

Run it where numpy and redis are installed (the project image has both).
"""
import argparse
import json
import platform
import statistics
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.cache_service import PromptCacheService  # noqa: E402

EMBEDDING_DIM = 1536
ANSWER_TEXT = "這是一段用來模擬知識庫答案的文字，長度與實際的客服答案相近。" * 15
DEFAULT_THRESHOLD_MS = 50.0


def build_values(count: int, seed: int = 0) -> tuple[list[bytes], list[bytes], list[float]]:
    """Synthetic cache values shaped like real ones: full-precision floats, a
    few hundred characters of answer text, a few sources. Returns the member
    names, the stored JSON bytes and a query embedding unrelated to all of them.
    """
    rng = np.random.default_rng(seed)
    members, values = [], []
    for i in range(count):
        embedding = rng.standard_normal(EMBEDDING_DIM)
        embedding /= np.linalg.norm(embedding)
        entry = {
            "question": f"合成問題 {i}",
            "answer": ANSWER_TEXT,
            "sources": [f"來源問題 {i}-{j}" for j in range(3)],
            "embedding": embedding.tolist(),
            "timestamp": 1_700_000_000 + i,
            "hit_count": 0,
        }
        members.append(f"prompt_cache:{i:016x}".encode())
        values.append(json.dumps(entry, ensure_ascii=False).encode())
    query = rng.standard_normal(EMBEDDING_DIM)
    query /= np.linalg.norm(query)
    return members, values, query.tolist()


class InMemoryRedis:
    """Just the commands a lookup that finds nothing to purge or hit issues.

    It counts what it served, because get_cached_response swallows every
    exception and returns None: without the counts, a stub that fails early
    would look like a very fast lookup.
    """

    def __init__(self, members: list[bytes], values: list[bytes]):
        self._members = list(reversed(members))  # newest first, like ZREVRANGE
        self._values = dict(zip(members, values))
        self.mget_calls = 0
        self.keys_served = 0

    def zrevrange(self, name, start, end):
        return self._members[start:end + 1]

    def mget(self, keys):
        self.mget_calls += 1
        self.keys_served += len(keys)
        return [self._values[k] for k in keys]


def verify_full_scan(stub: InMemoryRedis, result, count: int, lookups: int) -> None:
    """Fail loudly unless every lookup read all `count` entries and found no match."""
    if result is not None:
        raise SystemExit("benchmark invalid: the synthetic query matched an entry")
    if stub.mget_calls != lookups or stub.keys_served != lookups * count:
        raise SystemExit(
            f"benchmark invalid: expected {lookups} MGET calls reading {lookups * count} keys, "
            f"got {stub.mget_calls} calls reading {stub.keys_served}"
        )


def median_ms(fn, repeat: int) -> float:
    fn()  # warm-up
    samples = []
    for _ in range(repeat):
        start = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - start) * 1000)
    return statistics.median(samples)


def measure(count: int, repeat: int) -> dict:
    members, values, query = build_values(count)
    payload_mb = sum(len(v) for v in values) / 1e6
    stub = InMemoryRedis(members, values)
    service = PromptCacheService(stub, max_entries=count)

    parsed = [json.loads(v) for v in values]
    embeddings = [entry["embedding"] for entry in parsed]
    arrays = [np.array(e) for e in embeddings]
    query_array = np.array(query)

    def cosine():
        for vec in arrays:
            float(np.dot(query_array, vec) / (np.linalg.norm(query_array) * np.linalg.norm(vec)))

    lookup_ms = median_ms(lambda: service.get_cached_response(query), repeat)
    verify_full_scan(stub, service.get_cached_response(query), count, lookups=repeat + 2)

    return {
        "entries": count,
        "payload_mb": payload_mb,
        "json_ms": median_ms(lambda: [json.loads(v) for v in values], repeat),
        "numpy_ms": median_ms(lambda: [np.array(e) for e in embeddings], repeat),
        "cosine_ms": median_ms(cosine, repeat),
        # The real code path: decode, validate, convert and compare every entry.
        "lookup_ms": lookup_ms,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--entries", type=int, nargs="+", default=[50, 100])
    parser.add_argument("--repeat", type=int, default=30, help="timed runs per measurement (median reported)")
    parser.add_argument("--threshold-ms", type=float, default=DEFAULT_THRESHOLD_MS,
                        help="budget for one lookup at the default maximum of 50 entries")
    args = parser.parse_args()

    print(f"Python {platform.python_version()}, numpy {np.__version__}, "
          f"{platform.system()} {platform.machine()}, {args.repeat} runs, median in ms")
    print(f"{'entries':>8} {'payload MB':>11} {'json.loads':>11} {'np.array':>9} "
          f"{'cosine':>8} {'sum':>8} {'full lookup':>12}")
    results = [measure(count, args.repeat) for count in args.entries]
    for r in results:
        total = r["json_ms"] + r["numpy_ms"] + r["cosine_ms"]
        print(f"{r['entries']:>8} {r['payload_mb']:>11.2f} {r['json_ms']:>11.2f} {r['numpy_ms']:>9.2f} "
              f"{r['cosine_ms']:>8.2f} {total:>8.2f} {r['lookup_ms']:>12.2f}")

    at_default = next((r for r in results if r["entries"] == 50), None)
    if at_default is not None:
        verdict = "within" if at_default["lookup_ms"] <= args.threshold_ms else "OVER"
        print(f"\n50 entries, full lookup {at_default['lookup_ms']:.2f} ms: "
              f"{verdict} the {args.threshold_ms:.0f} ms budget")
        if verdict == "OVER":
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
