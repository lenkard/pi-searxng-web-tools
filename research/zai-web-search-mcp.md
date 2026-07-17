# Z.AI Web Search MCP integration research

Research date: 2026-07-17

## Question

How should the existing `web_search` tool integrate the user's Z.AI account through Z.AI's Web Search MCP connection while preserving SearXNG routing, normalization, caching, health, and fallback behavior?

## Executive finding

Use Z.AI's **remote Streamable HTTP MCP endpoint** behind a custom SearXNG engine. Do not use Z.AI's direct Web Search REST endpoint for this account, and do not expose Z.AI as a second Pi tool.

The account key successfully initialized the MCP server, listed its search tool, and performed a search. The same key received HTTP 429 / Z.AI error `1113` (“Insufficient balance or no resource package”) from the direct REST Web Search endpoint. This proves that the account's MCP entitlement and direct API balance are different for this key.

## Official Z.AI interfaces

### Current MCP endpoint

Z.AI's current MCP documentation configures a remote HTTP server at:

```text
https://api.z.ai/api/mcp/web_search_prime/mcp
```

Authentication is an HTTP header:

```text
Authorization: Bearer <api-key>
```

Z.AI documents this as a remote MCP service requiring no local server installation. Its current examples label the transport as `http`, `streamableHttp`, or `streamable-http`, depending on the client. A legacy SSE example also exists, but puts the API key in the URL query string; the current header-authenticated endpoint is preferable because it keeps credentials out of URLs and routine access logs.

Source: [Z.AI Web Search MCP Server](https://docs.z.ai/devpack/mcp/search-mcp-server)

### Advertised tool and actual tool

The prose documentation calls the tool `webSearchPrime`, but a live `tools/list` request on 2026-07-17 advertised:

```text
web_search_prime
```

The implementation must use protocol discovery/verification rather than trusting the prose spelling.

The live tool schema has these arguments:

| Argument | Required | Observed behavior |
|---|---:|---|
| `search_query` | yes | Recommended maximum of 70 characters |
| `search_domain_filter` | no | Restricts results to one whitelist domain |
| `search_recency_filter` | no | `oneDay`, `oneWeek`, `oneMonth`, `oneYear`, `noLimit` |
| `content_size` | no | `medium` (default, documented as 400–600 words) or `high` (documented as 2500 words) |
| `location` | no | `cn` for China or `us` for non-Chinese regions; server default is `cn` |

Source: live MCP `tools/list` from the documented endpoint; descriptive claims also appear in [Z.AI Web Search MCP Server](https://docs.z.ai/devpack/mcp/search-mcp-server).

### MCP protocol behavior observed

A live initialization requested MCP protocol `2025-06-18`; the Z.AI server negotiated `2024-11-05`, returned a session ID in `Mcp-Session-Id`, and advertised tool-list change support. Responses used `text/event-stream` even on the Streamable HTTP endpoint.

A successful tool call returned ten search results in this shape:

```json
{
  "title": "...",
  "link": "https://...",
  "content": "...",
  "refer": "ref_1"
}
```

The MCP `result.content[0].text` value was itself JSON-encoded, so the adapter must defensively decode the text until it obtains the result array (with a strict maximum number of decode passes).

The MCP specification says Streamable HTTP clients POST JSON-RPC messages to one endpoint, accept either JSON or SSE responses, retain a returned session ID, and use the protocol version negotiated during initialization. It also describes fallback to the deprecated HTTP+SSE transport, but Z.AI's current `/mcp` endpoint worked and should be the only initial target.

Source: [MCP Streamable HTTP transport specification](https://modelcontextprotocol.io/specification/2025-06-18/basic/transports)

## Direct REST Web Search is not usable by this account

Z.AI separately documents a direct Web Search API. It accepts `search_engine`, `search_query`, `count` (1–50), optional domain restriction, and recency filters, and returns a `search_result` array.

Sources:

- [Z.AI Web Search guide](https://docs.z.ai/guides/tools/web-search)
- [Z.AI Web Search API reference](https://docs.z.ai/api-reference/tools/web-search)

Live verification with the user's key on 2026-07-17 produced:

```text
HTTP 429
error.code = 1113
Insufficient balance or no resource package. Please recharge.
```

The MCP search succeeded immediately before/after this check. Therefore, the unfinished local engine that POSTs to `/api/paas/v4/web_search` would not integrate this account successfully and must be replaced rather than completed.

## Fit with the existing codebase

The current product interface is already deep:

```text
Pi web_search
  -> FastAPI /websearch
  -> SearXNG engine selection
  -> normalized results, cache, health, cooldown, fallback, and fusion
```

SearXNG's online engine processor calls an engine's `request(query, params)`, performs the configured HTTP request, then calls `response(resp)` to normalize results. The processor uses SearXNG's shared network layer and supports POST JSON requests.

Sources:

- [SearXNG online processor source](https://raw.githubusercontent.com/searxng/searxng/master/searx/search/processors/online.py)
- [SearXNG engine result documentation](https://docs.searxng.org/dev/engines/index.html)
- [SearXNG network layer source](https://raw.githubusercontent.com/searxng/searxng/master/searx/network/__init__.py)

MCP needs multiple HTTP exchanges: initialize, initialized notification, and tool call. A Z.AI SearXNG engine can hide those exchanges inside its implementation while presenting the normal SearXNG engine interface. This preserves the existing FastAPI wrapper as provider-neutral and lets the existing routing, result normalization, cache, health, cooldown, and fallback code work unchanged.

## Options considered

### 1. Direct Z.AI REST engine — rejected

It is the simplest transport and supports a result count, but this account is not entitled to it. The live request failed with error `1113`.

### 2. Register Z.AI MCP as a separate Pi tool — rejected

This would create a second search interface, bypass central routing/caching/health/fusion, make the model choose between overlapping tools, and fail the requirement to integrate Z.AI into the existing `web_search` behavior.

### 3. Add a Z.AI adapter to FastAPI — rejected

It would work technically, but it would move provider-specific protocol knowledge into the wrapper and split search providers across two routing layers. This weakens locality and conflicts with the repository convention that search providers live as SearXNG engines.

### 4. Add a generic provider/MCP abstraction now — rejected

There is only one required MCP-backed provider. A generic seam with one adapter would be hypothetical and would enlarge the interface without current leverage. Keep MCP handling private to the Z.AI engine; extract a generic module only if a second MCP provider arrives.

### 5. MCP-backed native SearXNG engine — selected

This keeps one caller interface (`web_search`), one provider registry (SearXNG), and one reliability path (the existing wrapper). Protocol complexity stays local to the engine.

## Parameter mapping and limitations

| Existing search input | Z.AI MCP mapping |
|---|---|
| `q` | `search_query` |
| `time_range=day` | `oneDay` |
| `time_range=month` | `oneMonth` |
| `time_range=year` | `oneYear` |
| no time range | `noLimit` |
| language/location | Start with configurable `location=us`; MCP exposes region rather than language |
| `max_results` | MCP exposes no count; Z.AI currently returns ten and the wrapper can truncate |
| `pageno` | Unsupported; engine must advertise `paging = false` |
| categories | Handled by SearXNG, not sent to MCP |

Use `content_size=medium` initially. `high` increases response size/cost and is unnecessary for discovery because `web_fetch` remains the canonical way to inspect a selected page.

The existing Pi tool only documents `day`, `month`, and `year`, so Z.AI's `oneWeek` capability does not require an interface expansion in the first slice.

## Security findings

- Use the current `/mcp` endpoint with a Bearer header, not the legacy SSE URL that embeds the key in the query string.
- Keep the key in the existing ignored Docker-secret path, mounted read-only at `/run/secrets/zai_api_key`.
- Never log request headers, MCP URLs containing credentials, or the key.
- The plaintext `/home/coder/zai.md` file was migrated during research to the ignored `searxng/secrets/zai_api_key` file with mode `0600`, then removed.
- Because the key was previously placed in conversation/plaintext, rotate it after the integration is verified.

## Open product decision

Should Z.AI be:

1. explicit-only at first (`engines=zai`), or
2. promoted after verification to the first default engine (`zai,bing,yep,mwmbl,wiby`)?

The staged recommendation is explicit-only for verification, then default-primary after one controlled relevance/latency check and confirmation of the account's MCP quota policy.
