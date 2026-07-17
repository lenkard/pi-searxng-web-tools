# SPDX-License-Identifier: AGPL-3.0-or-later
"""Z.AI Web Search engine backed by the remote MCP service."""

import json
import os
import typing as t
from pathlib import Path

from searx.exceptions import SearxEngineAPIException, SearxEngineTooManyRequestsException
from searx.network import post
from searx.result_types import EngineResults, MainResult

if t.TYPE_CHECKING:
    from searx.extended_types import SXNG_Response
    from searx.search.processors import OnlineParams

about = {
    "website": "https://z.ai",
    "official_api_documentation": "https://docs.z.ai/devpack/mcp/search-mcp-server",
    "use_official_api": True,
    "require_api_key": True,
    "results": "JSON",
    "description": "Z.AI Web Search through its remote MCP service.",
}

categories = ["general", "web"]
paging = False
time_range_support = True
language_support = False
safesearch = False
mcp_url = "https://api.z.ai/api/mcp/web_search_prime/mcp"
mcp_tool = "web_search_prime"
content_size = "medium"
location = "us"


def _secret() -> str:
    value = os.getenv("ZAI_API_KEY", "").strip()
    if value:
        return value
    try:
        return Path("/run/secrets/zai_api_key").read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _recency(value: str | None) -> str:
    return {
        "day": "oneDay",
        "week": "oneWeek",
        "month": "oneMonth",
        "year": "oneYear",
    }.get(value or "", "noLimit")


def init(_engine_settings: dict[str, t.Any]) -> None:
    if not _secret():
        raise SearxEngineAPIException("Z.AI MCP API key is missing")


def _raise_http_error(resp: "SXNG_Response") -> None:
    if resp.status_code in (401, 403):
        raise SearxEngineAPIException("Z.AI MCP authorization failed")
    if resp.status_code == 429:
        raise SearxEngineTooManyRequestsException(message="Z.AI MCP rate limit")
    if resp.status_code >= 400:
        raise SearxEngineAPIException(f"Z.AI MCP HTTP {resp.status_code}")


def _rpc_message(resp: "SXNG_Response") -> dict[str, t.Any]:
    _raise_http_error(resp)
    text = resp.text.strip()
    if not text:
        return {}
    if text.startswith("{"):
        try:
            value = json.loads(text)
        except json.JSONDecodeError as error:
            raise SearxEngineAPIException("Z.AI MCP returned malformed JSON") from error
        if isinstance(value, dict):
            return value
        raise SearxEngineAPIException("Z.AI MCP returned a non-object JSON-RPC message")

    messages: list[dict[str, t.Any]] = []
    data_lines: list[str] = []
    for line in text.splitlines() + [""]:
        if line.startswith("data:"):
            data_lines.append(line[5:].lstrip())
        elif not line and data_lines:
            try:
                value = json.loads("\n".join(data_lines))
            except json.JSONDecodeError as error:
                raise SearxEngineAPIException("Z.AI MCP returned malformed event-stream data") from error
            if isinstance(value, dict):
                messages.append(value)
            data_lines = []
    if not messages:
        raise SearxEngineAPIException("Z.AI MCP returned malformed event-stream data")
    return messages[-1]


def _rpc_result(resp: "SXNG_Response") -> dict[str, t.Any]:
    message = _rpc_message(resp)
    error = message.get("error")
    if error:
        if isinstance(error, dict):
            detail = str(error.get("message") or error.get("code") or "unknown error")
        else:
            detail = str(error)
        raise SearxEngineAPIException(f"Z.AI MCP error: {detail}")
    result = message.get("result")
    if not isinstance(result, dict):
        raise SearxEngineAPIException("Z.AI MCP response is missing a result object")
    return result


def _headers(api_key: str, session_id: str | None = None, protocol_version: str | None = None) -> dict[str, str]:
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
    }
    if session_id:
        headers["Mcp-Session-Id"] = session_id
    if protocol_version:
        headers["MCP-Protocol-Version"] = protocol_version
    return headers


def request(query: str, params: "OnlineParams") -> None:
    api_key = _secret()
    if not api_key:
        raise SearxEngineAPIException("Z.AI MCP API key is missing")

    initialize_response = post(
        mcp_url,
        headers=_headers(api_key),
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "searxng-zai", "version": "1.0.0"},
            },
        },
        raise_for_httperror=False,
    )
    initialize_result = _rpc_result(initialize_response)
    session_id = initialize_response.headers.get("Mcp-Session-Id")
    if not session_id:
        raise SearxEngineAPIException("Z.AI MCP initialization returned no session ID")
    protocol_version = str(initialize_result.get("protocolVersion") or "2024-11-05")
    session_headers = _headers(api_key, session_id, protocol_version)

    initialized_response = post(
        mcp_url,
        headers=session_headers,
        json={"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
        raise_for_httperror=False,
    )
    _raise_http_error(initialized_response)

    params["url"] = mcp_url
    params["method"] = "POST"
    params["headers"].update(session_headers)
    params["raise_for_httperror"] = False
    params["json"] = {
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/call",
        "params": {
            "name": mcp_tool,
            "arguments": {
                "search_query": query,
                "search_recency_filter": _recency(params.get("time_range")),
                "content_size": content_size,
                "location": location,
            },
        },
    }


def _decode_items(value: t.Any) -> list[dict[str, t.Any]]:
    for _ in range(3):
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
        if not isinstance(value, str):
            break
        try:
            value = json.loads(value)
        except json.JSONDecodeError as error:
            raise SearxEngineAPIException("Z.AI MCP tool returned malformed search results") from error
    raise SearxEngineAPIException("Z.AI MCP tool returned malformed search results")


def response(resp: "SXNG_Response") -> EngineResults:
    result = _rpc_result(resp)
    if result.get("isError"):
        raise SearxEngineAPIException("Z.AI MCP search tool reported an error")

    content = result.get("content") or []
    text_item = next(
        (item.get("text") for item in content if isinstance(item, dict) and item.get("type") == "text"),
        None,
    )
    if text_item is None:
        raise SearxEngineAPIException("Z.AI MCP search tool returned no text content")

    results = EngineResults()
    for item in _decode_items(text_item):
        url = item.get("link")
        if not url:
            continue
        results.add(MainResult(
            url=url,
            title=item.get("title", ""),
            content=item.get("content", ""),
        ))
    return results
