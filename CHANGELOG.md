# Changelog

## Unreleased (v1.5.0)

### Engine health monitoring and resilience

- Added an in-process engine-health monitor that probes each engine through SearXNG on a tiered schedule (general engines ~15 min, public CSEs ~2h to respect quotas) and persists state to a volume so restarts keep failure history.
- Chronically broken engines (configurable consecutive-failure threshold) are now auto-excluded from default searches, so single-engine failures no longer silently degrade results.
- Failures classify as `rate_limited` vs `broken`; recovery is detected and logged.

### Agent-facing observability

- New `GET /engines` and `GET /engines/health` endpoints expose per-engine status (healthy / degraded / rate_limited / broken / unknown), latency, last success, and failure counts as JSON for the Pi agent to read.
- `/health` now includes an `engine_health` summary and `broken_engines` list.
- Search responses now include `degraded`, `broken_engines`, and `excluded_broken` so the agent knows when results are impacted.

### Skill contingency and logging

- The `web_search` skill no longer hard-crashes when the backend is unreachable; it returns a degraded message with fallback suggestions so the agent can adapt.
- The skill surfaces chronically broken engines in its output.
- Added structured, greppable logging (`ENGINE_FAIL`, `ENGINE_BROKEN`, `ENGINE_RECOVER`, `SEARCH_DEGRADED`, `PROBE`) to aid diagnosis via `docker compose logs`.

### Auto query routing

- `engines=auto` routes the query to an intent-appropriate engine set via keyword heuristics (Reddit/opinions, academic, errors/stack traces, code, documents, social), falling back to the default chain when no rule matches.
- Auto-routed sets also exclude chronically broken engines for resilience. Explicit engine selection remains authoritative.

## v1.4.0 - 2026-07-12

### Supply-chain and maintenance

- Pinned the SearXNG and Python base images to immutable registry digests in both Dockerfiles.
- Pinned all Python dependencies in `api/requirements.txt` to exact versions.
- Added Dependabot configuration for Docker image digests, pip packages, and GitHub Actions, grouped to reduce PR noise.

### CI

- Added a GitHub-hosted CI workflow that builds both pinned images, boots the full Compose stack, and runs deterministic contract tests (health, empty-query/limit/pageno rejection, PyPI stays disabled) without depending on live search providers.

### Security

- Added SSRF protection to `web_fetch`: hosts resolving to private, loopback, link-local, or reserved ranges (including cloud metadata endpoints) are rejected with HTTP 400. The guard runs before the first request and on every redirect hop. An opt-out `WEB_FETCH_ALLOW_PRIVATE` flag exists for trusted internal deployments.

## v1.3.0 - 2026-07-12

### Search providers

- Added 29 selectable public OSINT Google CSEs for Reddit, social, documents, files, fact-checking, government, and other focused searches.
- Enabled keyless bot-friendly SearXNG providers including Crossref, GitLab, GitHub Code, Hacker News, OpenAlex, Stack Overflow, and Semantic Scholar.
- Disabled the broken PyPI HTML engine; PyPI currently serves a JavaScript challenge and upstream SearXNG issue #4093 remains open.

### SearXNG compatibility

- Added a patched SearXNG image that initializes custom Google CSE instances without the missing `supported_domains` trait.
- Persisted the patch in a derived Docker image and removed stale Python bytecode during builds.

### API reliability

- Reject empty queries, invalid pages/result limits, and unknown or disabled engines instead of silently running default providers.
- Convert knowledge-engine infoboxes, including Wikipedia answers, into normal agent-visible results.
- Preserve adaptive fallback, caching, cooldowns, result fusion, and upstream diagnostics.

### Testing

- Added the comprehensive search relevance/reliability benchmark script.
- Tested all 29 public CSEs, focused API engines, search modes, caching, pagination, aliases, fetching, validation, and concurrent requests.

## v1.2.2 - 2026-07-10

### Search engines

- Enabled SearXNG's keyless `google cse` engine after successful OCI testing.
- Made Google CSE the first adaptive fallback because it returned substantially stronger technical results than the other free engines.
- Kept automatic fallback and cooldown behavior because the shared CSE endpoint can still be rate-limited.

## v1.2.1 - 2026-07-10

### Packaging

- Marked Pi-provided SDK peer dependencies as optional so git package installation does not download a duplicate Pi runtime.

## v1.2.0 - 2026-07-10

### Compatibility

- Migrated the Pi extension SDK import and peer dependency to `@earendil-works/pi-coding-agent >=0.80.0`.
- Updated notification severity and tool error signaling for the current extension contract.

### Search reliability

- Updated the SearXNG engine set from tests on a new OCI datacenter IP.
- Added a configurable sequential fallback chain (`mojeek,yep,bing,wiby`) instead of fragile category-wide aggregation.
- Added selected/attempted engine metadata and upstream failure diagnostics.
- Added free `fast`, `balanced`, and `deep` search modes.
- Added quality-triggered fallback, URL deduplication, reciprocal-rank fusion, in-memory caching, and automatic engine cooldowns.
- Kept Codex/OpenAI search out of the routing path so search does not consume the user's Codex allowance.
- Added `SEARCH-PROVIDER-PLAN.md` with current provider research and a hybrid architecture recommendation.

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
