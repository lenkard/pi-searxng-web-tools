# Pi SearXNG Web Tools

Self-hosted web search and web page extraction for the [pi coding agent](https://github.com/mariozechner/pi-coding-agent).

This project contains:

- a SearXNG container configured with JSON API support and a small, reliable engine set
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
GET  /websearch?q=searxng&max_results=5&language=auto
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

By default, Compose binds both ports to `127.0.0.1` only. They are reachable from the Docker host, not directly from the internet.

Optional: require a shared API key for the FastAPI wrapper:

```bash
WEB_API_KEY='change-me-long-random-token' docker compose up -d --build
export PI_WEB_API_KEY='change-me-long-random-token'
```

4. Test:

```bash
curl 'http://localhost:8889/health'
curl 'http://localhost:8889/websearch?q=SearXNG%20API&max_results=2'
curl 'http://localhost:8889/webfetch?url=https%3A%2F%2Fdocs.searxng.org%2Fdev%2Fsearch_api.html&max_chars=800'
```

## Install as a pi package

This repository is a pi package. It has a `package.json` with a `pi.extensions` manifest that points to:

```text
pi-extension/web-search-fetch.ts
```

Install the extension globally with pi:

```bash
pi install git:github.com/lenkard/pi-searxng-web-tools
```

Or pin to a release tag:

```bash
pi install git:github.com/lenkard/pi-searxng-web-tools@v1.0.0
```

Or install it only for the current project:

```bash
pi install -l git:github.com/lenkard/pi-searxng-web-tools
```

You can also test without installing permanently:

```bash
pi -e git:github.com/lenkard/pi-searxng-web-tools
```

Then restart pi or run inside pi:

```text
/reload
```

### Manual extension install

If you already cloned the repo, you can also install the extension by copying it:

```bash
./install-extension.sh
```

or manually:

```bash
mkdir -p ~/.pi/agent/extensions
cp pi-extension/web-search-fetch.ts ~/.pi/agent/extensions/web-search-fetch.ts
```

## Registered pi tools

The extension registers:

```text
web_search
web_fetch
```

It also registers a status command:

```text
/webapi-status
```

## Important: the Docker/API endpoint is required

The pi package only installs the **pi extension**. The extension does not run SearXNG itself. It calls an HTTP API endpoint that must already be reachable.

You need both parts:

1. **Docker services** from this repo:
   - `searxng` on port `8888`
   - `webfetch-api` on port `8889`
2. **Pi package/extension** installed with `pi install` or copied manually.

The extension defaults to this API base URL:

```text
http://172.17.0.1:8889
```

This default is useful when pi runs inside a Docker container. In that case, `localhost` means "inside the pi container", not the Docker host. `172.17.0.1` is commonly the Docker bridge gateway that lets the pi container reach services published on the Docker host.

If pi runs directly on the same host as Docker, use localhost instead:

```bash
export PI_WEB_API_BASE_URL=http://localhost:8889
```

If the API runs on another machine, expose it only through a VPN or authenticated reverse proxy, then point pi at that host:

```bash
PI_WEB_API_BASE_URL=http://your-host:8889 pi
```

If you configured `WEB_API_KEY` on the API service, set the same key for pi:

```bash
PI_WEB_API_KEY='change-me-long-random-token' pi
```

Check connectivity from pi with:

```text
/webapi-status
```

## Tool parameters

### `web_search`

```json
{
  "q": "SearXNG API",
  "max_results": 10,
  "pageno": 1,
  "language": "auto",
  "categories": "general",
  "engines": "bing,github",
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

## Recommended SearXNG engine set

The example SearXNG `settings.yml` enables JSON results and keeps a small engine set that has tested well from Docker/container IPs:

```text
bing, github, stackoverflow, mdn, wikipedia, arxiv, pypi
```

This avoids noisier engines such as DuckDuckGo, Brave, Startpage and Mojeek, which can return CAPTCHA/403/429 errors from datacenter IPs.

## Benchmark notes

On a private Docker host, this setup tested around these rough timings:

- `web_search`: median about `0.85s`, average about `0.97s` across five general/dev queries.
- `web_fetch`: median about `0.08s`, average about `0.32s` across four static/article pages.

These numbers depend heavily on network, upstream engines, cache state and target websites. For public SearXNG timing comparisons, see `https://searx.space/`.

## Security notes

- Compose binds ports to `127.0.0.1` by default to prevent direct internet access.
- The FastAPI wrapper supports optional shared-key auth with `WEB_API_KEY`; pi sends it with `PI_WEB_API_KEY`.
- SearXNG itself has no API password by default; `server.secret_key` is not access control.
- Do not expose port `8889` or `8888` publicly without a reverse proxy, authentication, and rate limiting.
- Keep `searxng/settings.yml` private because it contains `server.secret_key`.
- Public SearXNG instances can attract abusive traffic. Keep this private unless you know how to operate a public instance safely.
- `/webfetch` fetches arbitrary URLs, so public exposure can create SSRF/open-proxy risk. Keep it private or protect it with auth and network filtering.

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
