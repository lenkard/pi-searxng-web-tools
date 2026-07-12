#!/usr/bin/env python3
"""Relevance and reliability benchmark for the web search API.

Runs a fixed set of queries across fast/balanced/deep modes, records latency,
result counts, top-3 precision, query-term coverage, freshness, cache hits, and
per-engine availability. Prints a markdown table and writes a JSON report.

Usage:
  python3 scripts/benchmark_search.py
  WEB_API_BASE=http://172.17.0.1:8889 python3 scripts/benchmark_search.py
  MODES=balanced python3 scripts/benchmark_search.py
  QUERIES_PER_CATEGORY=4 python3 scripts/benchmark_search.py
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import statistics
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from typing import Any

BASE = os.getenv("WEB_API_BASE", "http://172.17.0.1:8889").rstrip("/")
API_KEY = os.getenv("WEB_API_KEY", "")
MODES = [m.strip() for m in os.getenv("MODES", "fast,balanced,deep").split(",") if m.strip()]
PER = max(1, int(os.getenv("QUERIES_PER_CATEGORY", "3")))
REPEATS = max(1, int(os.getenv("REPEATS", "2")))
SLEEP = float(os.getenv("SLEEP", "0.6"))


# Query categories with relevance expectations used to score top-3 precision.
# "expected" = authoritative domains that should appear in the top results.
QUERIES: list[dict[str, Any]] = [
    {
        "q": "FastAPI lifespan documentation",
        "expected": ["fastapi.tiangolo.com", "docs.python.org"],
        "category": "technical-docs",
    },
    {
        "q": "Python 3.14 release notes",
        "expected": ["python.org", "docs.python.org"],
        "category": "technical-docs",
    },
    {
        "q": "docker compose networking static ip",
        "expected": ["docs.docker.com"],
        "category": "technical-docs",
    },
    {
        "q": "SearXNG google cse configuration",
        "expected": ["docs.searxng.org", "github.com"],
        "category": "technical-docs",
    },
    {
        "q": "numpy einsum notation explained",
        "expected": ["numpy.org", "docs.scipy.org"],
        "category": "technical-docs",
    },
    {
        "q": "Redis persistence RDB vs AOF",
        "expected": ["redis.io"],
        "category": "technical-docs",
    },
    {
        "q": "github actions reusable workflow inputs",
        "expected": ["docs.github.com"],
        "category": "technical-docs",
    },
    {
        "q": "OCI always free tier 2026 ARM",
        "expected": ["docs.oracle.com", "oracle.com"],
        "category": "current-info",
    },
    {
        "q": "latest stable Linux kernel version",
        "expected": ["kernel.org", "en.wikipedia.org"],
        "category": "current-info",
    },
    {
        "q": "nginx rate limiting request per second",
        "expected": ["nginx.com", "nginx.org"],
        "category": "technical-docs",
    },
    {
        "q": "asyncio gather vs wait differences",
        "expected": ["docs.python.org", "stackoverflow.com"],
        "category": "technical-docs",
    },
    {
        "q": "postgres jsonb index performance",
        "expected": ["postgresql.org", "www.postgresql.org"],
        "category": "technical-docs",
    },
]


def token_count(query: str) -> int:
    return len([t for t in re.findall(r"[a-z0-9]+", query.lower()) if len(t) >= 3])


def coverage(query: str, results: list[dict[str, Any]]) -> float:
    terms = {t for t in re.findall(r"[a-z0-9]+", query.lower()) if len(t) >= 3}
    if not terms or not results:
        return 0.0
    scores = []
    for r in results[:5]:
        haystack = f"{r.get('title') or ''} {r.get('content') or ''}".lower()
        scores.append(sum(1 for term in terms if term in haystack) / len(terms))
    return sum(scores) / len(scores)


def host(url: str) -> str:
    try:
        return urllib.parse.urlsplit(url).netloc.lower().lstrip("www.")
    except Exception:
        return ""


def top3_precision(results: list[dict[str, Any]], expected: list[str]) -> int:
    """1.0 if any expected authoritative host appears in top-3, else 0.0."""
    top_hosts = [host(r.get("url") or "") for r in results[:3]]
    return 1.0 if any(any(exp in h for exp in expected) for h in top_hosts) else 0.0


def success(results: list[dict[str, Any]]) -> bool:
    return len(results) > 0


def get_json(path: str, params: dict[str, Any], timeout: int = 90) -> tuple[int, dict[str, Any]]:
    url = BASE + path + "?" + urllib.parse.urlencode(params)
    headers = {"User-Agent": "pi-searxng-benchmark/1.2"}
    if API_KEY:
        headers["X-API-Key"] = API_KEY
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read()
        return resp.status, json.loads(body.decode("utf-8", errors="replace"))


def percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    k = (len(ordered) - 1) * p / 100
    floor = int(k)
    ceil = min(floor + 1, len(ordered) - 1)
    if floor == ceil:
        return ordered[floor]
    return ordered[floor] * (ceil - k) + ordered[ceil] * (k - floor)


def run_one(query_entry: dict[str, Any], mode: str, attempt: int) -> dict[str, Any]:
    q = query_entry["q"]
    expected = query_entry["expected"]
    start = time.perf_counter()
    row: dict[str, Any] = {
        "query": q,
        "category": query_entry["category"],
        "mode": mode,
        "attempt": attempt,
        "ok": False,
        "ms": 0.0,
        "results": 0,
        "selected_engine": None,
        "selected_engines": [],
        "attempted_engines": [],
        "skipped_engines": [],
        "failures": [],
        "quality_score": None,
        "cache_hit": False,
        "top3_hit": 0.0,
        "coverage": 0.0,
        "freshness_ok": False,
        "error": None,
    }
    try:
        status, data = get_json("/websearch", {"q": q, "max_results": 5, "mode": mode})
        ms = (time.perf_counter() - start) * 1000
        results = data.get("results", [])
        row.update(
            {
                "ok": success(results),
                "ms": ms,
                "results": len(results),
                "selected_engine": data.get("selected_engine"),
                "selected_engines": data.get("selected_engines", []),
                "attempted_engines": data.get("attempted_engines", []),
                "skipped_engines": data.get("skipped_engines", []),
                "failures": data.get("unresponsive_engines", []),
                "quality_score": data.get("quality_score"),
                "cache_hit": bool(data.get("cache_hit")),
                "top3_hit": top3_precision(results, expected),
                "coverage": round(coverage(q, results), 3),
            }
        )
        # Freshness: did any result with a year in title/content include a recent year?
        years = re.findall(r"20[12][0-9]", json.dumps(results))
        recent = any(int(y) >= 2024 for y in years) if years else False
        row["freshness_ok"] = recent or query_entry["category"] != "current-info"
    except urllib.error.HTTPError as err:
        row["ms"] = (time.perf_counter() - start) * 1000
        try:
            row["error"] = err.read().decode("utf-8", errors="replace")[:200]
        except Exception:
            row["error"] = str(err)
    except Exception as err:  # noqa: BLE001
        row["ms"] = (time.perf_counter() - start) * 1000
        row["error"] = str(err)[:200]
    return row


def summarize(mode: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    # For each query pick the best attempt (ok=True preferred, then lowest latency).
    by_query: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        by_query.setdefault(r["query"], []).append(r)
    best_rows: list[dict[str, Any]] = []
    for q, attempts in by_query.items():
        attempts.sort(key=lambda r: (not r["ok"], r["ms"]))
        best_rows.append(attempts[0])

    ok = [r for r in best_rows if r["ok"]]
    times = [r["ms"] for r in best_rows]
    counts = [r["results"] for r in best_rows]
    prec = [r["top3_hit"] for r in best_rows]
    cov = [r["coverage"] for r in best_rows]
    cache_hits = sum(1 for r in rows if r["cache_hit"])

    # Use all attempts for engine availability.
    engine_attempts: Counter[str] = Counter()
    engine_failures: Counter[str] = Counter()
    engine_success: Counter[str] = Counter()
    for r in rows:
        for e in r["attempted_engines"]:
            engine_attempts[e] += 1
        for pair in r["failures"]:
            name = pair[0] if isinstance(pair, list) else str(pair)
            engine_failures[name] += 1
        for e in r["selected_engines"]:
            engine_success[e] += 1

    return {
        "mode": mode,
        "queries": len(best_rows),
        "success_rate": round(len(ok) / len(best_rows), 3) if best_rows else 0.0,
        "top3_precision": round(sum(prec) / len(prec), 3) if prec else 0.0,
        "mean_coverage": round(sum(cov) / len(cov), 3) if cov else 0.0,
        "avg_results": round(sum(counts) / len(counts), 1) if counts else 0.0,
        "median_ms": round(statistics.median(times), 1) if times else 0.0,
        "p95_ms": round(percentile(times, 95) or 0.0, 1) if times else 0.0,
        "max_ms": round(max(times), 1) if times else 0.0,
        "cache_hits": cache_hits,
        "engine_attempts": dict(engine_attempts),
        "engine_success": dict(engine_success),
        "engine_failures": dict(engine_failures),
    }


def markdown(summary_rows: list[dict[str, Any]]) -> str:
    lines = [
        "# Search benchmark results",
        "",
        f"Generated: {dt.datetime.now(dt.UTC).isoformat()}",
        f"Endpoint: `{BASE}`  Modes: {', '.join(MODES)}  Repeats: {REPEATS}",
        "",
        "## Summary by mode",
        "",
        "| mode | queries | success rate | top-3 precision | mean coverage | avg results | median ms | p95 ms | max ms | cache hits |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for s in summary_rows:
        lines.append(
            f"| {s['mode']} | {s['queries']} | {s['success_rate']} | "
            f"{s['top3_precision']} | {s['mean_coverage']} | {s['avg_results']} | "
            f"{s['median_ms']} | {s['p95_ms']} | {s['max_ms']} | {s['cache_hits']} |"
        )

    # Engine availability across all attempts/modes
    all_attempts: Counter[str] = Counter()
    all_success: Counter[str] = Counter()
    all_fail: Counter[str] = Counter()
    for s in summary_rows:
        all_attempts.update(s["engine_attempts"])
        all_success.update(s["engine_success"])
        all_fail.update(s["engine_failures"])
    lines += ["", "## Engine reliability (all attempts)", ""]
    if all_attempts:
        lines.append("| engine | attempts | selected | failures | availability |")
        lines.append("|---|---:|---:|---:|---:|")
        for engine in sorted(all_attempts):
            attempts = all_attempts[engine]
            selected = all_success.get(engine, 0)
            failures = all_fail.get(engine, 0)
            availability = round((attempts - failures) / attempts, 3) if attempts else 0.0
            lines.append(f"| {engine} | {attempts} | {selected} | {failures} | {availability} |")
    else:
        lines.append("_No engine attempts recorded._")
    return "\n".join(lines) + "\n"


def main() -> int:
    if PER < 3:
        # Subset per category, keep ordering by category then query.
        per_cat: dict[str, list[dict[str, Any]]] = {}
        for q in QUERIES:
            per_cat.setdefault(q["category"], []).append(q)
        selected = []
        for cat, items in per_cat.items():
            selected.extend(items[:PER])
        queries = selected
    else:
        queries = QUERIES

    all_rows: list[dict[str, Any]] = []
    summaries: list[dict[str, Any]] = []
    print(f"Benchmarking {BASE} | modes={MODES} | queries={len(queries)} | repeats={REPEATS}", flush=True)
    for mode in MODES:
        mode_rows: list[dict[str, Any]] = []
        # First pass for each query (cold-ish), then repeats to exercise cache.
        for attempt in range(1, REPEATS + 1):
            for query in queries:
                row = run_one(query, mode, attempt)
                mode_rows.append(row)
                flag = "OK" if row["ok"] else "FAIL"
                print(
                    f"  [{mode}] try{attempt} {flag} {row['ms']:7.1f}ms "
                    f"n={row['results']} top3={row['top3_hit']} cov={row['coverage']} "
                    f"engine={row['selected_engine']} cache={row['cache_hit']} "
                    f"q={query['q'][:48]}",
                    flush=True,
                )
                time.sleep(SLEEP)
        s = summarize(mode, mode_rows)
        summaries.append(s)
        all_rows.extend(mode_rows)
        print(f"  -> {mode}: success={s['success_rate']} top3={s['top3_precision']} "
              f"median={s['median_ms']}ms", flush=True)

    md = markdown(summaries)
    out_dir = os.path.dirname(os.path.abspath(__file__))
    md_path = os.path.join(out_dir, "benchmark_results.md")
    json_path = os.path.join(out_dir, "benchmark_results.json")
    with open(md_path, "w", encoding="utf-8") as fh:
        fh.write(md)
    with open(json_path, "w", encoding="utf-8") as fh:
        json.dump(
            {
                "endpoint": BASE,
                "modes": MODES,
                "queries": queries,
                "repeats": REPEATS,
                "summaries": summaries,
                "rows": all_rows,
            },
            fh,
            indent=2,
        )
    print(f"\nWrote {md_path} and {json_path}", flush=True)
    print("\n" + md, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
