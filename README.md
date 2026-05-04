# Pi SearXNG Web Tools

Self-hosted web search and web page extraction for the [pi coding agent](https://github.com/mariozechner/pi-coding-agent).

This project contains:

- a SearXNG container configured with JSON API support
- a small FastAPI wrapper that exposes clean `websearch` and `webfetch` endpoints
- a pi extension that registers `web_search` and `web_fetch` tools

## Architecture

```text
pi agent
  │
  ├─ tool: web_search ─┐
  └─ tool: web_fetch  ─┤
                       ▼
              FastAPI wrapper :8889
               ├─ /websearch -> SearXNG :8888/search?format=json
               └─ /webfetch  -> httpx + trafilatura extraction
```

## Endpoints

FastAPI wrapper:

```text
GET  /health
GET  /websearch?q=searxng&max_results=5
POST /websearch
GET  /webfetch?url=https://example.com&max_chars=20000
POST /webfetch
```

Compatibility aliases:

```text
GET  /api/web_search
POST /api/web_search
GET  /api/web_fetch
POST /api/web_fetch
```

Direct SearXNG JSON API:

```text
GET /search?q=test&format=json
```

## Quick start with Docker Compose

1. Clone the repository.

2. Create the SearXNG config:

```bash
cp searxng/settings.yml.example searxng/settings.yml
python3 - <<'PY'
import secrets
print(secrets.token_urlsafe(48))
PY
```

Put the generated value into `server.secret_key` in `searxng/settings.yml`.

3. Start the services:

```bash
docker compose up -d --build
```

4. Test:

```bash
curl 'http://localhost:8889/health'
curl 'http://localhost:8889/websearch?q=SearXNG%20API&max_results=2'
curl 'http://localhost:8889/webfetch?url=https%3A%2F%2Fdocs.searxng.org%2Fdev%2Fsearch_api.html&max_chars=800'
```

## Install the pi extension

Install globally for the current user:

```bash
./install-extension.sh
```

Or copy manually:

```bash
mkdir -p ~/.pi/agent/extensions
cp pi-extension/web-search-fetch.ts ~/.pi/agent/extensions/web-search-fetch.ts
```

Then restart pi or run inside pi:

```text
/reload
```

The extension registers:

```text
web_search
web_fetch
```

It also registers a status command:

```text
/webapi-status
```

## Important network note

The extension defaults to:

```text
http://172.17.0.1:8889
```

This was chosen for pi running in a container while the Docker host publishes the API on port `8889`.

If pi runs directly on the same host as Docker, use localhost instead:

```bash
export PI_WEB_API_BASE_URL=http://localhost:8889
```

You can set any base URL with:

```bash
PI_WEB_API_BASE_URL=http://your-host:8889 pi
```

## Tool parameters

### `web_search`

```json
{
  "q": "SearXNG API",
  "max_results": 10,
  "pageno": 1,
  "language": "all",
  "categories": "general",
  "engines": "duckduckgo,brave",
  "time_range": "month"
}
```

### `web_fetch`

```json
{
  "url": "https://docs.searxng.org/dev/search_api.html",
  "max_chars": 20000
}
```

## What was changed during the original setup

The manual setup used these Docker resources:

- network: `websearch_net`
- volumes: `searxng_config`, `searxng_cache`, `webfetch_api_app`
- containers: `searxng`, `webfetch-api`
- published ports: `8888` for SearXNG, `8889` for the FastAPI wrapper

The SearXNG `settings.yml` enabled JSON results:

```yaml
search:
  formats:
    - html
    - json
```

The Pi extension was installed at:

```text
~/.pi/agent/extensions/web-search-fetch.ts
```

An older `npm:@ollama/pi-web-search` package was removed from pi settings because it registered the same `web_search` and `web_fetch` tool names and caused a conflict.

## Security notes

- This setup has no authentication by default.
- Do not expose port `8889` or `8888` publicly without a reverse proxy, authentication, and rate limiting.
- Keep `searxng/settings.yml` private because it contains `server.secret_key`.
- Public SearXNG instances can attract abusive traffic. Keep this private unless you know how to operate a public instance safely.

## Troubleshooting

Check containers:

```bash
docker compose ps
docker compose logs searxng
docker compose logs webfetch-api
```

Check pi extension health:

```text
/webapi-status
```

If pi says `web_search` conflicts with another extension, remove or disable any other extension/package that registers the same tool names.

## License

MIT
