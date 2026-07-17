# Web-search provider benchmark

Date: 2026-07-17  
Endpoint: private Kinkaid `web_search` API over WireGuard  
Purpose: determine whether Z.AI and Serper improve result quality, reliability, and latency enough to change provider order.

## Method

The repository's existing five-query benchmark was used unchanged:

1. `cutest cat breeds`
2. `python fastapi tutorial`
3. `docker compose network static ip`
4. `nginx proxy manager access list basic auth`
5. `searxng configuration engines`

First, `scripts/benchmark.py` measured the deployed default chain end to end. Then the same queries were sent once to each configuration with five requested results:

- explicit Z.AI, `fast`
- explicit Serper, `fast`
- explicit Bing, `fast` — representative of the previous free-chain primary
- current `auto`, `balanced`

No Google CSEs were called. Requests were paced by one second. Provider comparison consumed five explicit Serper requests and five explicit Z.AI requests, plus auto requests where needed.

Metrics:

- non-empty successful searches;
- cold request latency;
- the wrapper's existing lexical relevance/domain-diversity `quality_score`;
- unique domains in five results;
- an authoritative source in the top three for four technical queries.

Expected authoritative domains were fixed before evaluation: FastAPI official docs; Docker docs/forums or Stack Overflow; Nginx Proxy Manager's site/GitHub; and SearXNG official docs.

This is a small functional benchmark, not a statistically significant long-duration study.

## Existing default-chain benchmark

The repository benchmark completed successfully:

| Metric | Result |
|---|---:|
| Search success | 5/5 (100%) |
| Results per query | 5/5 |
| Median search latency | 3.60 s |
| Average search latency | 3.60 s |
| Search latency range | 3.29–3.96 s |
| Fetch success | 5/5 (100%) |
| Median fetch latency | 270 ms |

At that point Z.AI was the first provider, explaining the roughly 3.6-second search latency.

## Provider comparison

| Configuration | Non-empty success | Median latency | Mean quality | Mean unique domains | Authoritative source in top 3 |
|---|---:|---:|---:|---:|---:|
| Z.AI | 4/5 | 3.78 s* | 0.928* | 4.75* | 3/4 |
| Serper | **5/5** | **0.93 s** | **0.897** | 4.40 | **4/4** |
| Bing baseline | 5/5 | 0.26 s | 0.637 | 4.00 | 1/4 |
| Auto/balanced | **5/5** | **0.96 s** | **0.894** | 4.20 | **4/4** |

`*` Z.AI aggregates only its four successful searches. Its fifth query timed out at SearXNG's 15-second limit and returned no results.

The auto/balanced run selected Serper because Z.AI's timeout triggered the existing health/cooldown system. Responses were marked `degraded` only because Z.AI was excluded; Serper itself returned successful results.

## Result-quality examples

### `python fastapi tutorial`

- Serper ranked the official FastAPI tutorial **#1**.
- Z.AI ranked it **#2**, behind a YouTube tutorial.
- Bing's top three were Python.org, Python downloads, and an online compiler; no FastAPI result appeared.

### `docker compose network static ip`

- Serper returned Stack Overflow **#1** and Docker documentation **#2**.
- Z.AI returned Stack Overflow **#1** and Docker documentation **#3**.
- Bing returned generic Docker product/introductory pages rather than the requested networking configuration.

### `searxng configuration engines`

- Serper's top three were all relevant official SearXNG configuration pages.
- Bing found general SearXNG pages, with official documentation at #2.
- Z.AI timed out and returned no results.

### `cutest cat breeds`

- Serper and Z.AI returned the same three directly relevant breed-list pages.
- Bing returned unrelated Spanish-language real-estate/Infonavit pages.

## Findings

### Do the hosted providers improve results?

**Yes, substantially.** Compared with the previous Bing primary:

- Serper increased mean quality from `0.637` to `0.897` — about **41% higher** by the wrapper's existing heuristic.
- Authoritative technical sources appeared in the top three for **4/4** Serper queries versus **1/4** Bing queries.
- Serper avoided the severe query/language mismatches observed from Bing.

### Which provider should be primary?

**Serper should be primary.** It produced the best combined result:

- 5/5 non-empty searches;
- authoritative source in the top three for every technical query;
- median latency below one second;
- no Serper provider failure during the run.

Z.AI's successful results were relevant and diverse, but it was roughly four times slower than Serper and timed out on one of five queries. It is useful as a secondary/test provider, not the routine primary.

## Recommended order

```text
serper,zai,bing,yep,mwmbl,wiby
```

Expected behavior with the existing modes:

- `fast`: normally returns Serper's first successful result set in about one second;
- `balanced`: stops after Serper when quality is strong, otherwise adds Z.AI or the next available provider;
- if Serper quota or service fails: Z.AI assumes the request, followed by the free fallback engines;
- the 15-minute cache reduces paid requests and makes repeated searches much faster.

## Caveats

- Five queries are enough to expose large relevance and latency differences, but not enough to estimate long-term uptime.
- Search results change over time and by geography.
- Z.AI's timeout was one observation, not a measured failure rate.
- Serper's quota and account balance still need operational monitoring; this benchmark did not probe quota exhaustion.
