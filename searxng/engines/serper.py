# SPDX-License-Identifier: AGPL-3.0-or-later
"""Serper Google Search API engine."""

import datetime as dt
import os
import typing as t

from searx.exceptions import SearxEngineAPIException, SearxEngineTooManyRequestsException
from searx.result_types import EngineResults, MainResult

if t.TYPE_CHECKING:
    from searx.extended_types import SXNG_Response
    from searx.search.processors import OnlineParams

about = {
    "website": "https://serper.dev",
    "official_api_documentation": "https://serper.dev",
    "use_official_api": True,
    "require_api_key": True,
    "results": "JSON",
    "description": "Google Search results through the Serper API.",
}

categories = ["general", "web"]
paging = True
time_range_support = True
language_support = True
safesearch = False
page_size = 10
api_url = "https://google.serper.dev/search"


def _secret() -> str:
    value = os.getenv("SERPER_API_KEY", "").strip()
    if value:
        return value
    try:
        from pathlib import Path
        return Path("/run/secrets/serper_api_key").read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _time_range(value: str | None) -> str | None:
    return {"day": "qdr:d", "week": "qdr:w", "month": "qdr:m", "year": "qdr:y"}.get(value or "")


def init(_engine_settings: dict[str, t.Any]) -> None:
    if not _secret():
        raise SearxEngineAPIException("Serper API key is missing")


def request(query: str, params: "OnlineParams") -> None:
    params["url"] = api_url
    api_key = _secret()
    if not api_key:
        raise SearxEngineAPIException("Serper API key is missing")
    params["method"] = "POST"
    params["raise_for_httperror"] = False
    params["headers"]["X-API-KEY"] = api_key
    params["headers"]["Content-Type"] = "application/json"
    body: dict[str, t.Any] = {
        "q": query,
        "num": page_size,
        "page": params["pageno"],
    }
    locale = params.get("searxng_locale", "auto")
    if locale and locale not in ("all", "auto"):
        body["hl"] = locale.split("-")[0]
    time_range = _time_range(params.get("time_range"))
    if time_range:
        body["tbs"] = time_range
    params["json"] = body


def response(resp: "SXNG_Response") -> EngineResults:
    if resp.status_code in (401, 403):
        raise SearxEngineAPIException("Serper authorization failed")
    if resp.status_code == 429:
        raise SearxEngineTooManyRequestsException(message="Serper rate limit")
    if resp.status_code >= 400:
        raise SearxEngineAPIException(f"Serper HTTP {resp.status_code}")

    results = EngineResults()
    for item in (resp.json().get("organic") or []):
        url = item.get("link")
        if not url:
            continue
        published = item.get("date")
        try:
            published = dt.datetime.fromisoformat(published) if published else None
        except (TypeError, ValueError):
            published = None
        results.add(MainResult(
            url=url,
            title=item.get("title", ""),
            content=item.get("snippet", ""),
            publishedDate=published,
        ))
    return results
