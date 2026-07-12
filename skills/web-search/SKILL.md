---
name: web-search
description: Canonical workflow for Pi web_search and web_fetch, including safe engine use, result-quality recovery, health behavior, testing, and troubleshooting. Use whenever searching or fetching web content, investigating weak search results, checking the web tools, or diagnosing search failures.
---

# Web Search

## Normal workflow

1. Call `web_search` with `engines="auto"` and `mode="balanced"`.
2. Inspect titles, snippets, URLs, and any upstream-failure note.
3. Use `web_fetch` on the most relevant primary sources before relying on details or citing them.
4. If results are weak, refine the query once. Use `deep` only for explicitly broad research.
5. Use an explicit engine only when the user explicitly requests that source.

Never probe, sweep, benchmark, or cycle through engines during normal use. Never send multiple Google CSEs or retry a rate limit. The API owns routing, pacing, fallback, cooldowns, failure confirmation, and recovery.

## Troubleshooting

- No useful results, no failure: make the query more specific and retry once with `auto`.
- Upstream engine unavailable or rate-limited: do not retry or switch through engines; allow API fallback or report the limitation.
- Search API unavailable: run `/webapi-status`. Retry once only for a transient connection failure.
- A known URL is available: use `web_fetch` directly.
- Fetch fails: verify the URL is public HTTP(S); private-network targets are intentionally blocked.

## Safe verification

For an ordinary user-requested check, make one `auto`/`balanced` search and fetch one returned public URL. That verifies the complete tool path without testing every engine.

Repository checks for maintainers:

```bash
PYTHONPATH=api python3 -m unittest discover -s api -p 'test_*.py' -v
python3 api/app.py
python3 scripts/benchmark.py
```

Use passive `/health` and `/engines/health` reads for diagnosis. Do not create all-engine or repeated-CSE tests. If an operator explicitly requires a single CSE canary, issue only one request and stop immediately on any rate-limit diagnostic.
