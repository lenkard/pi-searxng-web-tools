# Pi SearXNG Web Tools

Self-hosted web search and web page extraction for the [pi coding agent](https://github.com/mariozechner/pi-coding-agent).

Current release: `v1.6.1`. See [`CHANGELOG.md`](CHANGELOG.md) for security, deployment, and benchmark notes.

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
GET  /engines
GET  /engines/health
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

2. Create the environment file with the SearXNG secret outside `settings.yml`:

```bash
cp .env.example .env
python3 - <<'PY'
import secrets
print(secrets.token_urlsafe(48))
PY
```

Put the generated value into `SEARXNG_SECRET` in `.env`. The committed `searxng/settings.yml` does not contain secrets.

3. Start the services:

```bash
docker compose up -d --build
```

By default, Compose binds both ports to `127.0.0.1` only. They are reachable from the Docker host, not directly from the internet.

If pi runs in another Docker container and must reach the wrapper through the Docker bridge gateway, bind only the FastAPI wrapper to that private gateway address, not to `0.0.0.0`:

```bash
WEB_API_BIND=172.17.0.1 docker compose up -d --build
```

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
pi install git:github.com/lenkard/pi-searxng-web-tools@v1.6.1
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
http://172.25.0.7:8889
```

This repository's default points to the private Kinkaid deployment over WireGuard. Override it for any other installation with `PI_WEB_API_BASE_URL`.

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
  "time_range": "month",
  "mode": "balanced"
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

The example SearXNG `settings.yml` enables JSON results and keeps a small engine set that tested well from this Docker/container environment:

```text
bing, yep, mwmbl, wiby, wikipedia, github, arxiv, crossref, gitlab, github code, hackernews, openalex, reddit, stackoverflow, semantic scholar, mojeek, google cse
```

Additional bot-friendly engines enabled for explicit selection:

```text
crossref, gitlab, github code, hackernews, openalex, reddit, stackoverflow, semantic scholar
```

The configuration also registers 29 disabled-by-default public specialized CSEs from the reviewed OSINT collections. Select them explicitly with names such as `cse reddit`, `cse documents`, `cse social`, or `cse fact checks`. These third-party CSEs are not controlled by this project; some currently return Google parsing errors through SearXNG even when their hosted browser interface still works.

Operational notes from testing on a new OCI datacenter IP (2026-07-10):

- `google cse`, `bing`, `mojeek`, `yep`, `mwmbl`, and `wiby` returned results during testing. Google CSE gave the strongest technical result set; Mwmbl and Wiby have much smaller indexes.
- `google cse` uses SearXNG's keyless CSE engine with a shared public search-engine identifier. It is not the official metered Custom Search JSON API and may still be rate-limited.
- `brave` returned HTTP 429, `duckduckgo` and `startpage` returned CAPTCHA, `qwant` denied access, and `yahoo` had protocol errors.
- The direct `google` scraper is marked inactive and `stackexchange` was not available in the tested SearXNG 2026.7.9 image.
- Upstream behavior is IP- and time-dependent. Even currently working scraping engines may later block a datacenter IP; use an official API provider as the primary source for reliable production use.

The wrapper uses `bing,yep,mwmbl,wiby` as a **sequential fallback chain**. Google CSE remains available for focused and explicit searches but is not routine default traffic because all CSEs share one Google-facing residential IP. Override the chain without rebuilding:

```bash
SEARXNG_DEFAULT_ENGINES=bing,yep,mwmbl,wiby docker compose up -d
```

Callers can always override this behavior with the `engines` tool parameter; an explicit comma-separated value uses SearXNG's normal aggregation.

Free search modes:

- `fast` stops at the first non-empty engine response.
- `balanced` (default) scores relevance and domain diversity, querying one additional free engine only when the first response is weak.
- `deep` combines up to three free engines using URL deduplication and reciprocal-rank fusion.

The wrapper caches successful searches for 15 minutes by default. CAPTCHA, 429, and explicit rate-limit responses activate a cooldown; a CSE rate limit cools the shared Google-CSE group. Automatic routing skips unavailable engines and falls back to the default chain.

Engine health at `/engines/health` is driven by real searches. Success immediately marks an engine healthy. A non-rate-limit failure becomes degraded and receives one delayed confirmation request; a repeated failure becomes temporarily broken, then returns as a runtime canary after cooldown. Rate limits are never immediately retried. Healthy and unused engines are not probed, and observations older than a day are reported as stale. Google-CSE requests are limited to one CSE per request and globally spaced by 10 seconds. Configure this with `ENGINE_COOLDOWN_SECONDS`, `CONFIRM_FAILURE_DELAY_SECONDS`, `HEALTH_STALE_SECONDS`, and `CSE_MIN_INTERVAL_SECONDS`.

No Codex/OpenAI search is used, so searches do not consume the user's Codex allowance.

## Nginx Proxy Manager / proxynet deployment

For a deployment behind Nginx Proxy Manager, use the optional Compose override:

```bash
docker network create --subnet 172.19.0.0/16 proxynet 2>/dev/null || true
docker compose -f docker-compose.yml -f docker-compose.proxynet.yml up -d --build
```

This attaches both services to the external `proxynet` network with stable internal names/IPs:

```text
searxng      -> 172.19.0.10
webfetch-api -> 172.19.0.11
webfetch     -> 172.19.0.11
```

In Nginx Proxy Manager, create a proxy host for SearXNG:

```text
Domain Names: search.example.com
Scheme: http
Forward Hostname / IP: searxng
Forward Port: 8080
```

Recommended NPM settings:

```text
Force SSL: enabled
HTTP/2 Support: enabled
Access List: enabled for private use
```

Do not expose `webfetch-api` publicly unless it is strongly protected; it can fetch arbitrary URLs.

## Benchmark notes

On the tested private Docker host, the internal Pi/tools path was:

```text
Pi/tools -> webfetch-api:8889 -> searxng:8080
```

Run the included benchmark script:

```bash
python3 scripts/benchmark.py
# or
WEB_API_BASE=http://localhost:8889 python3 scripts/benchmark.py
```

Small functional benchmark results from the tested host:

- `web_search`: 5/5 success, median `~0.51-0.69s`, average `~0.63-0.73s`, max `~1.04s`.
- Direct internal SearXNG: 5/5 success, median `~0.52s`, average `~0.62s`, max `~1.01s`.
- `web_fetch`: 5/5 success, median `~0.24-0.36s`, average `~0.39-0.44s`, max `~1.06s`.
- Public NPM ACL block test: 5/5 returned `403`, around `15-26ms`.

These numbers depend heavily on network, upstream engines, cache state and target websites. For public SearXNG timing comparisons, see `https://searx.space/`.

## Security notes

- Compose binds ports to `127.0.0.1` by default to prevent direct internet access. For container-to-host access, prefer a private bridge/VPN bind such as `WEB_API_BIND=172.17.0.1`; avoid `0.0.0.0`.
- The FastAPI wrapper supports optional shared-key auth with `WEB_API_KEY`; pi sends it with `PI_WEB_API_KEY`.
- SearXNG itself has no API password by default; `server.secret_key` is not access control.
- Do not expose port `8889` or `8888` publicly without a reverse proxy, authentication, and rate limiting.
- Keep `.env` private because it contains `SEARXNG_SECRET` and may contain `WEB_API_KEY`.
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
