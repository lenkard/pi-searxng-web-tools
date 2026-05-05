#!/usr/bin/env python3
"""Small functional benchmark for the web search/fetch API.

Usage:
  python3 scripts/benchmark.py
  WEB_API_BASE=http://localhost:8889 python3 scripts/benchmark.py
"""

from __future__ import annotations

import json
import os
import statistics
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

BASE = os.getenv("WEB_API_BASE", "http://172.17.0.1:8889").rstrip("/")
API_KEY = os.getenv("WEB_API_KEY", "")

SEARCH_QUERIES = [
    "cutest cat breeds",
    "python fastapi tutorial",
    "docker compose network static ip",
    "nginx proxy manager access list basic auth",
    "searxng configuration engines",
]

FETCH_URLS = [
    "https://en.wikipedia.org/wiki/Cat",
    "https://example.com/",
    "https://docs.python.org/3/library/json.html",
    "https://fastapi.tiangolo.com/tutorial/",
    "https://en.wikipedia.org/wiki/Searx",
]


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


def get_json(path: str, params: dict[str, Any], timeout: int = 40) -> tuple[int, dict[str, Any]]:
    url = BASE + path + "?" + urllib.parse.urlencode(params)
    headers = {"User-Agent": "pi-searxng-web-tools-benchmark/1.0"}
    if API_KEY:
        headers["X-API-Key"] = API_KEY
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read()
        return resp.status, json.loads(body.decode("utf-8", errors="replace"))


def summarize(name: str, rows: list[dict[str, Any]]) -> None:
    ok = [row for row in rows if row["ok"]]
    times = [row["ms"] for row in ok]
    print(f"\n## {name}")
    print(
        f"total={len(rows)} success={len(ok)} failed={len(rows) - len(ok)} "
        f"success_rate={len(ok) / len(rows) * 100:.1f}%"
    )
    if times:
        print(
            f"min={min(times):.1f}ms avg={statistics.mean(times):.1f}ms "
            f"median={statistics.median(times):.1f}ms p95={percentile(times, 95):.1f}ms "
            f"max={max(times):.1f}ms"
        )
    for row in rows:
        if "results" in row:
            extra = f" results={row.get('results')}"
        else:
            extra = f" chars={row.get('chars')} truncated={row.get('truncated')}"
        error = (" ERROR=" + row["error"][:120].replace("\n", " ")) if row.get("error") else ""
        print(f"{str(row['status']):>4} {row['ms']:>8.1f}ms {row['label']}{extra}{error}")


def run_search_benchmark() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for query in SEARCH_QUERIES:
        start = time.perf_counter()
        try:
            status, data = get_json("/websearch", {"q": query, "max_results": 5})
            ms = (time.perf_counter() - start) * 1000
            rows.append(
                {
                    "label": query,
                    "status": status,
                    "ms": ms,
                    "ok": status == 200,
                    "results": len(data.get("results", [])),
                }
            )
        except urllib.error.HTTPError as err:
            ms = (time.perf_counter() - start) * 1000
            rows.append(
                {
                    "label": query,
                    "status": err.code,
                    "ms": ms,
                    "ok": False,
                    "results": 0,
                    "error": err.read().decode("utf-8", errors="replace"),
                }
            )
        except Exception as err:  # noqa: BLE001 - benchmark should record all failures
            ms = (time.perf_counter() - start) * 1000
            rows.append({"label": query, "status": "ERR", "ms": ms, "ok": False, "results": 0, "error": str(err)})
        time.sleep(0.5)
    return rows


def run_fetch_benchmark() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for url in FETCH_URLS:
        start = time.perf_counter()
        try:
            status, data = get_json("/webfetch", {"url": url, "max_chars": 5000})
            ms = (time.perf_counter() - start) * 1000
            rows.append(
                {
                    "label": url,
                    "status": status,
                    "ms": ms,
                    "ok": status == 200,
                    "chars": data.get("chars", 0),
                    "truncated": data.get("truncated", False),
                }
            )
        except urllib.error.HTTPError as err:
            ms = (time.perf_counter() - start) * 1000
            rows.append(
                {
                    "label": url,
                    "status": err.code,
                    "ms": ms,
                    "ok": False,
                    "chars": 0,
                    "truncated": False,
                    "error": err.read().decode("utf-8", errors="replace"),
                }
            )
        except Exception as err:  # noqa: BLE001 - benchmark should record all failures
            ms = (time.perf_counter() - start) * 1000
            rows.append(
                {
                    "label": url,
                    "status": "ERR",
                    "ms": ms,
                    "ok": False,
                    "chars": 0,
                    "truncated": False,
                    "error": str(err),
                }
            )
        time.sleep(0.5)
    return rows


if __name__ == "__main__":
    print(f"Benchmarking {BASE}")
    summarize("websearch benchmark", run_search_benchmark())
    summarize("webfetch benchmark", run_fetch_benchmark())
