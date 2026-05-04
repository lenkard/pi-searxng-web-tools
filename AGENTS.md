# AGENTS.md

Guidance for AI coding agents working in this repository.

## Project summary

This repo implements self-hosted web search/fetch tooling for the pi coding agent:

1. `searxng` container provides metasearch and JSON search results.
2. `api/app.py` exposes a small FastAPI wrapper for search and fetch.
3. `pi-extension/web-search-fetch.ts` registers pi tools named `web_search` and `web_fetch`.

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
curl 'http://localhost:8889/websearch?q=SearXNG%20API&max_results=2'
```

Test fetch:

```bash
curl 'http://localhost:8889/webfetch?url=https%3A%2F%2Fdocs.searxng.org%2Fdev%2Fsearch_api.html&max_chars=800'
```

Install pi extension:

```bash
./install-extension.sh
```

## File ownership and secrets

- Never commit `searxng/settings.yml`; it contains `server.secret_key`.
- Use `searxng/settings.yml.example` for public documentation.
- Keep `.gitignore` protecting local SearXNG config and caches.

## Design constraints

- Keep the pi tool names `web_search` and `web_fetch` unless intentionally changing compatibility.
- Keep API paths `/websearch` and `/webfetch`; `/api/web_search` and `/api/web_fetch` are compatibility aliases.
- The pi extension default API base URL is `http://172.17.0.1:8889` for containerized pi environments. Users can override with `PI_WEB_API_BASE_URL`.

## Security

This project is intended for private/local usage. If exposing publicly, add authentication, rate limiting, and a reverse proxy.
