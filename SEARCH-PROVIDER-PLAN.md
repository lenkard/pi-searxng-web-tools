# Search provider plan

Research date: 2026-07-10. Prices and free tiers change; verify the linked provider page before implementation.

## Finding from the OCI deployment

A one-day-old OCI IP did not make scraping-based search reliable. In the first controlled test:

| SearXNG engine | Result |
|---|---|
| Bing | Working initially |
| Mojeek | Working initially, then temporarily suspended after repeated tests |
| Yep | Working |
| Mwmbl | Working, but a small independent index |
| Wiby | Working, but a small/independent index |
| Brave | HTTP 429 |
| DuckDuckGo | CAPTCHA |
| Startpage | CAPTCHA |
| Qwant | Access denied |
| Yahoo | HTTP protocol errors |

This confirms that IP age is not the main issue. Datacenter ASN reputation, engine anti-bot policy, geography, and request pattern matter more. SearXNG should remain a fallback, not the reliability layer.

## Provider comparison

| Provider | Free use observed | Paid price observed | Fit for Pi |
|---|---:|---:|---|
| [Exa](https://exa.ai/pricing) | Up to 20,000 requests/month | Usage-dependent; verify dashboard | Best first candidate for agent-oriented semantic search and content retrieval |
| [Tavily](https://www.tavily.com/pricing) | 1,000 API credits/month, no card | $0.008/credit pay-as-you-go shown | Strong research-oriented search; advanced operations consume more credits |
| [Brave Search API](https://brave.com/search/api/) | $5 monthly credit; card required | $5/1,000 requests for the Search plan shown | Independent web index, predictable general search, good fallback |
| [Serper](https://serper.dev/) | 2,500 introductory queries, no card | From $1/1,000 at 50k credits; cheaper at volume | Google-like SERP results; free allocation appears introductory rather than monthly |
| [Google Custom Search JSON API](https://developers.google.com/custom-search/v1/overview) | 100/day for existing users | $5/1,000 | Not available to new customers according to Google documentation |
| [SearchApi](https://www.searchapi.io/pricing) | 100 introductory requests | Plans from $40/month; $4/1,000 shown | Broad SERP support, but poor value for this small deployment |
| [OpenRouter web search](https://openrouter.ai/docs/guides/features/web-search) | Not generally free | Exa/Parallel/Perplexity shown at $0.005/request | Convenient when OpenRouter billing is already configured |
| [Perplexity Search API](https://docs.perplexity.ai/getting-started/pricing) | No recurring free tier confirmed | $5/1,000 searches; fetch URL $0.0005 each shown | Simple managed search/fetch fallback |
| SearXNG | Unlimited software, no API fee | VPS and operational cost | Free fallback and vertical search; unreliable for general engines on VPS IPs |

## Free vertical sources

Route technical queries to first-party APIs before spending general-search credits:

- GitHub REST/search APIs for repositories, issues, releases, and code where permitted.
- Stack Exchange API for programming questions.
- PyPI and npm registry APIs for packages.
- arXiv, Crossref, and OpenAlex for scholarly material.
- Wikimedia APIs for encyclopedic topics.
- RSS/Atom feeds for known news and release sources.

Each source needs its own rate-limit policy and attribution compliance.

## Recommended provider order

1. **Exa primary** while its recurring 20,000-request free tier remains available.
2. **First-party vertical APIs** for GitHub, packages, Stack Exchange, and academic queries.
3. **Brave API fallback** for independent general-web coverage; its monthly credit currently covers about 1,000 searches.
4. **Tavily optional deep-research provider** rather than spending its credits on every basic query.
5. **SearXNG last fallback**, using only engines currently healthy on this server.

If Exa's free-tier terms do not fit the project after account verification, invert steps 1 and 3: Brave primary, Tavily for deep research, SearXNG fallback.

## What Pi and other agent projects are doing

The current Pi ecosystem generally uses provider APIs and fallback chains rather than relying on one self-hosted scraper:

- [`pi-web-access`](https://github.com/nicobailon/pi-web-access) routes among OpenAI/Codex search, Exa MCP, Brave, Parallel, Tavily, Perplexity, and Gemini. It uses normal HTTP extraction first, then Jina Reader/provider extraction for blocked or JavaScript-heavy pages.
- [`pi-search-hub`](https://github.com/ronnieops/pi-search-hub) supports SearXNG alongside many hosted providers, sequential fallback, URL deduplication, and reciprocal-rank fusion.
- [`pi-exa`](https://github.com/junnjiee/pi-exa) uses Exa's hosted MCP service for keyless search/fetch and reserves keyed APIs for deeper work.
- [`pi-simple-web-tools`](https://github.com/jillesme/pi-simple-web-tools) uses Exa for discovery and direct HTTP/Readability for fetching, with Chromium only as a lazy fallback for JavaScript pages.
- Provider-native agent tools from OpenAI, Anthropic, Gemini, and xAI perform search server-side and return citations/domain controls. The agent does not personally browse a consumer search-results page.
- LangChain/CrewAI-style agents commonly integrate Tavily, Serper, Brave, or provider-native tools. Self-hosted UIs such as Open WebUI support SearXNG but also expose hosted providers because no-key search backends are frequently rate-limited.

The common pattern is therefore: **search API or hosted index for discovery; direct HTTP extraction for ordinary pages; specialized reader/browser fallback only for difficult pages**.

### Why Chromium is not the primary search solution

Headless Chromium helps render a JavaScript application after a URL is known. It does not fix search-engine blocking from a datacenter ASN. Consumer search engines inspect IP reputation, cookies, request behavior, TLS/browser fingerprinting, and account state; automated Chromium can still receive CAPTCHA or suspension while consuming much more CPU and memory.

Use Chromium selectively for content extraction, not to automate Google/Brave/DuckDuckGo result pages. Hosted search providers resolve the bot problem by operating an authorized API or their own crawl/index/proxy infrastructure. SearXNG cannot remove upstream anti-bot controls; it only coordinates engines.

## Proposed implementation

Create a provider-neutral backend interface:

```text
SearchProvider.search(query, options) -> normalized results + diagnostics
ContentProvider.fetch(url, options) -> extracted document + metadata
```

Normalized search result fields:

```text
title, url, snippet, published_at, provider, engine, rank, score
```

Routing modes:

- `fast`: one primary provider; low latency and cost.
- `balanced`: primary provider plus vertical source when query intent matches.
- `deep`: two providers, result fusion, and fetch top sources.

Reliability behavior:

- Per-provider timeout and circuit breaker.
- Retry only transient 429/5xx responses and honor `Retry-After`.
- Cache normalized searches by query/options.
- Canonicalize and deduplicate URLs.
- Fuse multiple rankings with reciprocal-rank fusion.
- Return provider failures in diagnostics instead of silently returning an empty list.
- Track success rate, relevant result in top 3, latency, 429/CAPTCHA rate, and cost per successful query.

## Rollout

1. Keep the deployed private SearXNG service as a baseline.
2. Add the provider interface without changing the Pi tool names.
3. Implement Exa and SearXNG adapters first.
4. Add Brave and Tavily adapters behind environment variables.
5. Add query-intent routing for vertical APIs.
6. Run a fixed relevance benchmark and a 72-hour health probe.
7. Select defaults from measured quality and cost, not only latency.
