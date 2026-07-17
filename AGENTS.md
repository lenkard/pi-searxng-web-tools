# AGENTS.md

Guidance for AI coding agents working in this repository.

## Project summary

This repo implements self-hosted web search/fetch tooling for the pi coding agent:

1. `searxng` container provides metasearch and JSON search results.
2. `api/app.py` exposes a small FastAPI wrapper for search and fetch.
3. `pi-extension/web-search-fetch.ts` registers pi tools named `web_search` and `web_fetch`.
4. `package.json` makes the repo installable as a pi package via `pi install git:github.com/lenkard/pi-searxng-web-tools`.

## Important commands

Start services:

```bash
docker compose up -d --build
```

Check service health:

```bash
curl http://localhost:8889/health
```

Test search:

```bash
curl 'http://localhost:8889/websearch?q=SearXNG%20API&max_results=2&language=auto'
```

Test fetch:

```bash
curl 'http://localhost:8889/webfetch?url=https%3A%2F%2Fdocs.searxng.org%2Fdev%2Fsearch_api.html&max_chars=800'
```

Install as pi package:

```bash
pi install git:github.com/lenkard/pi-searxng-web-tools
```

Install pi extension manually from a clone:

```bash
./install-extension.sh
```

## File ownership and secrets

- `searxng/settings.yml` is committed and is the engine-list source of truth.
- Never put `server.secret_key` in it; the real secret belongs in ignored `.env` as `SEARXNG_SECRET`.
- Keep `.gitignore` protecting `.env`, local databases, and caches.

## Design constraints

- Keep the pi tool names `web_search` and `web_fetch` unless intentionally changing compatibility.
- Keep API paths `/websearch` and `/webfetch`; `/api/web_search` and `/api/web_fetch` are compatibility aliases.
- Keep Google CSE out of routine defaults. Deployments with provider keys use `serper,zai,bing,yep,mwmbl,wiby`; deployments without them use `bing,yep,mwmbl,wiby`. CSEs remain available for focused searches.
- Docker Compose must bind published ports to `127.0.0.1` by default. For container-to-host access, document `WEB_API_BIND=<private bridge/VPN IP>`; do not use `0.0.0.0` without auth/firewall requirements.
- The FastAPI wrapper supports optional shared-key auth with `WEB_API_KEY`; the pi extension sends `PI_WEB_API_KEY` as `X-API-Key`.
- The pi extension default API base URL is the private Kinkaid WireGuard address `http://172.25.0.7:8889`. Other deployments must override it with `PI_WEB_API_BASE_URL`.
- The pi package installs only the extension. It must not assume Docker services are running; document that the SearXNG/FastAPI endpoint is required separately.
- Never bulk-test CSEs repeatedly. Health is driven by real searches; only a non-rate-limit failure receives one delayed confirmation request. Keep the API guardrails of one CSE per request and global CSE pacing.

## Security

This project is intended for private/local usage. If exposing publicly, add authentication, rate limiting, reverse proxy protections, and SSRF/network egress filtering for `/webfetch`.
