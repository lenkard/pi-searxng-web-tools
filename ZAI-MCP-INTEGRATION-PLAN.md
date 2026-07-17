# Z.AI MCP integration plan

Status: approved and implemented locally; production deployment pending

Research: [`research/zai-web-search-mcp.md`](research/zai-web-search-mcp.md)

## Problem statement

The existing `web_search` tool needs a reliable Z.AI-backed search route without creating a competing Pi tool or bypassing SearXNG's routing, cache, health, cooldown, diagnostics, and fallback behavior.

The current uncommitted Z.AI proof of concept calls Z.AI's direct REST Web Search endpoint. That endpoint rejects the user's account key with HTTP 429 / error `1113`, while Z.AI's Web Search MCP endpoint successfully searches with the same key. The integration must therefore speak MCP.

## Solution

Implement Z.AI as an MCP-backed native SearXNG engine and Serper as a native SearXNG API engine. The modules hide provider transport, authentication, error classification, and result normalization behind SearXNG's existing engine interface.

The external product interface remains unchanged:

```text
web_search(q, options)
  -> FastAPI /websearch
  -> SearXNG
  -> Z.AI MCP engine or existing fallback engines
```

No Z.AI-specific code will be added to the Pi extension or FastAPI wrapper.

## Design decisions

### Seam

The seam is the existing SearXNG engine interface. Callers and tests exercise Z.AI through the same engine registration used by every other search source.

Do not create a provider-neutral MCP interface yet. There is one MCP adapter, so a generic seam would be hypothetical. Keep helper functions private to the Z.AI engine and extract only after a second MCP provider creates real variation.

### Transport

Use only:

```text
POST https://api.z.ai/api/mcp/web_search_prime/mcp
Authorization: Bearer <Docker secret>
Accept: application/json, text/event-stream
Content-Type: application/json
```

Per search, use a fresh MCP session for the first implementation:

1. `initialize`
2. `notifications/initialized`
3. `tools/call` for `web_search_prime`

A fresh session avoids shared-session locking, stale-session retry, and JSON-RPC ID coordination. Measure its latency before considering session reuse. Do not optimize into a global session until evidence shows the extra round trips are material.

The engine will parse both `application/json` and `text/event-stream`, retain the negotiated protocol version and `Mcp-Session-Id`, and treat malformed/missing protocol data as an engine failure.

### Tool arguments

Send:

- `search_query`: SearXNG query
- `search_recency_filter`: `day -> oneDay`, `month -> oneMonth`, `year -> oneYear`, otherwise `noLimit`
- `content_size`: `medium`
- `location`: configurable, default `us`

Do not send unsupported count or paging arguments. Register the engine with `paging = false` and `time_range_support = true`.

### Result mapping

Map MCP results to SearXNG `MainResult`:

```text
link    -> url
title   -> title
content -> content
```

Ignore `refer` for ranking. Preserve only fields understood by the existing normalized result path. The wrapper remains responsible for truncating to `max_results`, deduplicating, scoring, and fusing.

### Error behavior

- HTTP 401/403: authorization/API exception
- HTTP 429: too-many-requests exception
- MCP JSON-RPC `error`: API exception with a sanitized message
- MCP tool `isError=true`: API exception with a sanitized message
- Missing session ID, malformed SSE, malformed nested JSON, or invalid result fields: API exception
- Never include authorization headers or secret values in exceptions/logs

These errors will flow through SearXNG diagnostics into the wrapper's existing health/cooldown/fallback behavior.

### Secret handling

Keep the key at:

```text
searxng/secrets/zai_api_key
```

It is ignored by Git, mode `0600`, and mounted as `/run/secrets/zai_api_key`. The key must not appear in settings, `.env`, URLs, source, tests, CI, or logs.

Rotate the key after the integration is verified because it previously existed in plaintext/conversation history.

### Serper provider

After the initial plan was approved, the user expanded the scope to complete Serper as another general-search provider. Serper uses its official Search API, supports paging and time ranges, and follows Z.AI in the default chain:

```text
zai,serper,bing,yep,mwmbl,wiby
```

## Testing decisions

### Primary deterministic seam

Test the Z.AI SearXNG engine interface with the network calls mocked:

1. initialization accepts an SSE response, records session ID, and uses the server-negotiated protocol version;
2. initialized notification includes the session header;
3. tool call uses `web_search_prime` and correct argument mapping;
4. successful nested MCP content becomes `MainResult` objects;
5. both JSON and SSE envelopes parse;
6. direct arrays, once-encoded arrays, and the observed twice-encoded text parse within a bounded decode loop;
7. 401/403, 429, JSON-RPC errors, `isError`, missing session, and malformed data map to the right SearXNG exceptions;
8. no test fixture or failure string contains a real key.

These tests exercise externally visible engine behavior rather than private implementation details.

### Existing wrapper seam

Retain existing FastAPI unit/contract tests. Add only the minimum assertions needed to show that the registered engine appears in `/engines` and can be explicitly selected without changing the public `web_search` schema.

CI must not call live Z.AI and must not require a real secret. Supply a non-secret placeholder only where SearXNG engine initialization requires one, while all network behavior remains mocked.

### Controlled live verification

After deterministic tests pass, run exactly one explicit live query:

```text
/websearch?q=<controlled query>&engines=zai&mode=fast&max_results=5
```

Verify:

- results are non-empty and normalized;
- attempted/selected engine diagnostics identify Z.AI;
- the key does not appear in logs;
- a repeated identical request is served from the wrapper cache;
- no broad provider sweep or repeated quota test is performed.

Then run one normal default-chain query only after deciding whether Z.AI should become the primary default.

## Rollout

### Slice 1 — Explicit Z.AI MCP search

**Blocked by:** None.

**Delivers:** A user can call the existing `web_search` with explicit Z.AI or Serper engines and receive normalized results through SearXNG.

Acceptance criteria:

- the direct REST proof is replaced by MCP transport;
- deterministic protocol/result/error tests pass;
- Compose mounts only the Z.AI Docker secret needed by this slice;
- Serper request, result, missing-key, and rate-limit tests pass;
- one controlled live search for each provider passes;
- no public Pi/FastAPI interface changes.

### Slice 2 — Resilient default routing

**Blocked by:** Slice 1.

**Delivers:** Normal `engines=auto` searches can use Z.AI as the primary route and fall back to existing SearXNG engines when Z.AI is unavailable.

Acceptance criteria:

- production default chain is `zai,bing,yep,mwmbl,wiby` (if approved);
- Z.AI failures appear in current diagnostics and trigger existing fallback behavior;
- successful repeated searches use the existing cache;
- README, engine list, security notes, and changelog describe MCP rather than direct REST;
- one controlled default-route query verifies fallback metadata without sweeping engines.

### Slice 3 — Production secret rotation and deployment

**Blocked by:** Slice 2.

**Delivers:** The verified integration is deployed to Kinkaid with a rotated key and rollback path.

Acceptance criteria:

- rotate the exposed Z.AI key in the Z.AI console;
- install the replacement only as the Docker secret on Kinkaid;
- preserve Kinkaid's existing `.env` and unrelated secrets;
- rebuild/restart only the changed SearXNG service (and wrapper only if configuration requires it);
- verify `/health`, `/engines`, one explicit Z.AI search, one normal search, and logs;
- retain the previous image/commit for rollback.

## Out of scope

- A second Pi search tool for Z.AI
- A generic MCP-provider framework
- Z.AI direct REST Web Search
- Z.AI chat-completions web search
- High-content MCP mode
- Exposing domain filtering or week recency in the public Pi tool in the first change
- Pagination for Z.AI MCP
- Load testing, quota probing, or engine sweeps

## Approved decisions

The user approved:

1. **Test seam:** mocked SearXNG engine interfaces, followed by one controlled live query per provider.
2. **Rollout:** explicit verification first, then Z.AI primary and Serper secondary in the default provider chain.
3. **Expanded scope:** complete Serper as a general-search provider rather than discarding it.
