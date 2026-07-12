# Changelog

## v1.6.0 - 2026-07-12

### Changed

- Replaced scheduled engine sweeps with runtime-driven health: real searches now provide the primary success and failure observations.
- A non-rate-limit failure receives one delayed confirmation request; rate limits are never immediately retried and Google CSEs retain their shared cooldown.
- Confirmed failures become temporarily unavailable, then return as runtime canaries after cooldown; old observations are reported as stale instead of permanently blocking an engine.

## v1.5.4 - 2026-07-12

### Fixed

- Restored the shared Google-CSE cooldown from persisted health state on startup, so restarting the API cannot bypass an active upstream backoff.

## v1.5.3 - 2026-07-12

### Fixed

- Docker Compose now forwards the documented probe, logging, and private-fetch settings from `.env` to the API container instead of silently ignoring overrides.

## v1.5.2 - 2026-07-12

### Fixed

- Replaced burst CSE health sweeps with a rotation of one CSE at a time across the configured two-hour interval.
- Added a shared cooldown for all Google-backed CSEs and made rate-limit state expire instead of remaining current for hours.
- `engines=auto` now skips unavailable focused engines and falls back to the safe default chain.
- Real searches now update engine health; rate limits no longer count toward the broken-engine threshold.
- Changed the default chain to `bing,yep,mwmbl,wiby`, keeping Google CSE available for focused searches without using it for routine traffic.
- Expanded SSRF rejection to all non-global and multicast IPv4/IPv6 addresses using Python's standard library.
- General engines are probed before CSE rotation, fixing slow startup classification.

### Maintenance

- Added routing, health, and SSRF unit tests to CI.
- Removed the high-volume relevance benchmark; `scripts/benchmark.py` remains the single routine benchmark.
- Updated stale versions and deployment documentation.

## v1.5.1 - 2026-07-12

### Fixed

- Skill `DEFAULT_BASE_URL` corrected from `172.17.0.1` (Docker bridge, only works from a container on the API host) to `172.25.0.7` (Kinkaid via WireGuard), the address the agent host actually reaches the API on.

## v1.5.0 - 2026-07-12

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

### Single source of truth for engines

- The API now derives its engine allowlist from SearXNG `settings.yml` at startup (the `keep_only` built-ins plus the custom Google CSE entries), eliminating the duplicated engine list that previously had to be edited in two places.
- `settings.yml` is mounted read-only into the API container; an explicit `SEARXNG_ALLOWED_ENGINES` env override still wins.
- Added `scripts/list_engines.py` to regenerate the README engine table from `settings.yml` on demand.

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
