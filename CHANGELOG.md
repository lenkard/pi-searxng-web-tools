# Changelog

## v1.1.0 - 2026-05-05

### Security

- Moved the SearXNG `server.secret_key` out of `searxng/settings.yml`.
- Added `SEARXNG_SECRET` environment variable support through Docker Compose.
- Added `.env.example` and kept the real `.env` ignored by git.
- Made `searxng/settings.yml` safe to version-control.
- Documented Nginx Proxy Manager ACL protection for public SearXNG access.
- Documented that `webfetch-api` should remain private because it can fetch arbitrary URLs.

### Deployment

- Added `docker-compose.proxynet.yml` for Nginx Proxy Manager deployments using an external `proxynet` Docker network.
- Documented stable internal Docker DNS/IPs:
  - `searxng -> 172.19.0.10`
  - `webfetch-api -> 172.19.0.11`
  - `webfetch -> 172.19.0.11`
- Documented NPM proxy host configuration:
  - `search.example.com -> http://searxng:8080`
- Added `searxng/limiter.toml` to avoid SearXNG missing limiter config warnings when the limiter is disabled.

### Search reliability

- Updated the recommended SearXNG engine set based on real container/IP testing.
- Removed brittle custom engine overrides from the example settings.
- Documented known engine behavior:
  - Bing can return irrelevant results from some datacenter IPs.
  - DuckDuckGo and Brave can trigger CAPTCHA/429 rate limits.
  - `stackexchange` is the correct SearXNG engine name for Stack Overflow-style results.

### Tooling

- Added `scripts/benchmark.py` for functional benchmarking of `/websearch` and `/webfetch`.
- Added benchmark notes for the tested deployment.

### Verified deployment status

The tested deployment verified:

- `web_search` working.
- `web_fetch` working.
- Internal Docker DNS working.
- SearXNG fixed internal IP working.
- Nginx Proxy Manager proxy working.
- NPM ACL blocking unauthenticated public access.
- SearXNG secret externalized via environment variable.
- Recent logs clean after final tests.
