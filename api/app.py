from fastapi import FastAPI, HTTPException, Query, Request
from pydantic import BaseModel, HttpUrl
from typing import Any, Literal, Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
import copy
import os
import re
import secrets
import time

import httpx
import trafilatura
from bs4 import BeautifulSoup

SEARXNG_URL = os.getenv("SEARXNG_URL", "http://searxng:8080/search")
SEARXNG_DEFAULT_ENGINES = os.getenv("SEARXNG_DEFAULT_ENGINES", "mojeek,yep,bing,mwmbl,wiby").strip()
SEARCH_CACHE_TTL_SECONDS = max(0, int(os.getenv("SEARCH_CACHE_TTL_SECONDS", "900")))
ENGINE_COOLDOWN_SECONDS = max(10, int(os.getenv("ENGINE_COOLDOWN_SECONDS", "300")))
BALANCED_QUALITY_THRESHOLD = min(1.0, max(0.0, float(os.getenv("BALANCED_QUALITY_THRESHOLD", "0.58"))))
USER_AGENT = os.getenv("WEB_API_USER_AGENT", "Mozilla/5.0 (compatible; private-web-api/1.0)")
WEB_API_KEY = os.getenv("WEB_API_KEY", "")

SEARCH_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
ENGINE_COOLDOWNS: dict[str, tuple[float, str]] = {}

app = FastAPI(title="Private Web Search/Fetch API", version="1.0.0")


class SearchBody(BaseModel):
    q: str
    max_results: int = 10
    pageno: int = 1
    language: str = "auto"
    categories: Optional[str] = None
    engines: Optional[str] = None
    time_range: Optional[str] = None
    mode: Literal["fast", "balanced", "deep"] = "balanced"


class FetchBody(BaseModel):
    url: HttpUrl
    max_chars: int = 20000


def require_api_key(request: Request) -> None:
    """Require a shared secret when WEB_API_KEY is configured."""
    if not WEB_API_KEY:
        return

    authorization = request.headers.get("authorization", "")
    bearer = authorization.removeprefix("Bearer ").strip() if authorization.startswith("Bearer ") else ""
    key = request.headers.get("x-api-key") or bearer
    if not key or not secrets.compare_digest(key, WEB_API_KEY):
        raise HTTPException(status_code=401, detail="Missing or invalid API key")


def forwarded_client_headers(request: Request) -> dict[str, str]:
    """Forward a client IP hint to SearXNG to keep bot-detection logs clean."""
    forwarded_for = request.headers.get("x-forwarded-for")
    real_ip = request.headers.get("x-real-ip")
    client_host = request.client.host if request.client else None

    headers = {"User-Agent": USER_AGENT}
    if forwarded_for:
        headers["X-Forwarded-For"] = forwarded_for
    if real_ip or client_host:
        headers["X-Real-IP"] = real_ip or client_host or "127.0.0.1"
    return headers


def canonical_url(raw_url: str) -> str:
    """Normalize URLs for deduplication without changing the returned URL."""
    try:
        parsed = urlsplit(raw_url)
        filtered_query = urlencode([
            (key, value)
            for key, value in parse_qsl(parsed.query, keep_blank_values=True)
            if not key.lower().startswith("utm_")
            and key.lower() not in {"fbclid", "gclid", "mc_cid", "mc_eid"}
        ])
        path = parsed.path.rstrip("/") or "/"
        return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), path, filtered_query, ""))
    except Exception:
        return raw_url.strip()


def result_quality(query: str, results: list[dict[str, Any]]) -> float:
    """Cheap relevance signal used only to decide whether another free engine is needed."""
    if not results:
        return 0.0

    query_tokens = {
        token for token in re.findall(r"[a-z0-9]+", query.lower())
        if len(token) >= 3 and token not in {"and", "the", "for", "with", "from", "what", "how"}
    }
    coverage_scores = []
    domains = set()
    for result in results:
        title = str(result.get("title") or "").lower()
        content = str(result.get("content") or "").lower()
        searchable = f"{title} {title} {content}"
        if query_tokens:
            coverage_scores.append(sum(token in searchable for token in query_tokens) / len(query_tokens))
        try:
            domains.add(urlsplit(str(result.get("url") or "")).netloc.lower())
        except Exception:
            pass

    count_score = min(len(results), 5) / 5
    diversity_score = min(len(domains), 4) / min(len(results), 4) if results else 0
    coverage_score = sum(coverage_scores) / len(coverage_scores) if coverage_scores else 0.5
    return round(0.35 * count_score + 0.20 * diversity_score + 0.45 * coverage_score, 3)


def fuse_results(result_lists: list[list[dict[str, Any]]], max_results: int) -> list[dict[str, Any]]:
    """Deduplicate and rank multi-engine results with reciprocal-rank fusion."""
    merged: dict[str, dict[str, Any]] = {}
    for results in result_lists:
        for rank, item in enumerate(results, start=1):
            key = canonical_url(str(item.get("url") or ""))
            if not key:
                continue
            if key not in merged:
                merged[key] = {"item": dict(item), "rrf_score": 0.0, "engines": set()}
            entry = merged[key]
            entry["rrf_score"] += 1 / (60 + rank)
            if item.get("engine"):
                entry["engines"].add(str(item["engine"]))
            if len(str(item.get("content") or "")) > len(str(entry["item"].get("content") or "")):
                entry["item"]["content"] = item.get("content")

    ranked = sorted(merged.values(), key=lambda entry: (-entry["rrf_score"], canonical_url(str(entry["item"].get("url") or ""))))
    output = []
    for entry in ranked[:max_results]:
        item = entry["item"]
        item["score"] = round(entry["rrf_score"], 6)
        item["source_engines"] = sorted(entry["engines"])
        output.append(item)
    return output


def cooldown_reason(diagnostics: list[Any]) -> Optional[str]:
    text = " ".join(str(item) for item in diagnostics).lower()
    markers = ("captcha", "too many requests", "access denied", "suspended", "rate limit", "429")
    return next((marker for marker in markers if marker in text), None)


def cache_key(body: SearchBody) -> str:
    return body.model_dump_json(exclude_none=True)


async def do_search(body: SearchBody, request: Request):
    key = cache_key(body)
    now = time.monotonic()
    cached = SEARCH_CACHE.get(key)
    if cached and now - cached[0] <= SEARCH_CACHE_TTL_SECONDS:
        result = copy.deepcopy(cached[1])
        result["cache_hit"] = True
        return result
    if cached:
        SEARCH_CACHE.pop(key, None)

    base_params = {
        "q": body.q,
        "format": "json",
        "pageno": body.pageno,
        "language": body.language,
    }
    for param_name in ("categories", "time_range"):
        value = getattr(body, param_name)
        if value:
            base_params[param_name] = value

    # Explicit caller input keeps SearXNG's normal comma-separated aggregation.
    # The configured default is a sequential fallback chain, avoiding a blocked
    # engine poisoning a combined SearXNG response.
    engine_attempts = [body.engines] if body.engines else [
        engine.strip() for engine in SEARXNG_DEFAULT_ENGINES.split(",") if engine.strip()
    ]
    if not engine_attempts:
        engine_attempts = [None]

    max_results = max(1, min(body.max_results, 50))
    target_lists = 1 if body.engines or body.mode == "fast" else (2 if body.mode == "balanced" else 3)
    result_lists: list[list[dict[str, Any]]] = []
    response_data: dict[str, Any] = {"query": body.q, "answers": [], "suggestions": []}
    selected_engines = []
    attempted_engines = []
    skipped_engines = []
    diagnostics = []
    last_error = None

    async with httpx.AsyncClient(timeout=30, headers=forwarded_client_headers(request)) as client:
        for engine in engine_attempts:
            engine_name = engine or "default"
            cooldown = ENGINE_COOLDOWNS.get(engine_name)
            if cooldown and cooldown[0] > now:
                skipped_engines.append(engine_name)
                diagnostics.append([engine_name, f"local cooldown: {cooldown[1]}"])
                continue
            if cooldown:
                ENGINE_COOLDOWNS.pop(engine_name, None)

            params = dict(base_params)
            if engine:
                params["engines"] = engine
            attempted_engines.append(engine_name)
            try:
                response = await client.get(SEARXNG_URL, params=params)
                response.raise_for_status()
                candidate = response.json()
            except Exception as error:
                last_error = error
                diagnostics.append([engine_name, f"request failed: {error}"])
                ENGINE_COOLDOWNS[engine_name] = (now + min(ENGINE_COOLDOWN_SECONDS, 60), "request failure")
                continue

            candidate_diagnostics = candidate.get("unresponsive_engines", [])
            diagnostics.extend(candidate_diagnostics)
            reason = cooldown_reason(candidate_diagnostics)
            if reason:
                ENGINE_COOLDOWNS[engine_name] = (now + ENGINE_COOLDOWN_SECONDS, reason)

            raw_results = []
            for item in candidate.get("results", []):
                raw_results.append(
                    {
                        "title": item.get("title"),
                        "url": item.get("url"),
                        "content": item.get("content"),
                        "engine": item.get("engine"),
                        "score": item.get("score"),
                        "publishedDate": item.get("publishedDate") or item.get("pubdate"),
                    }
                )

            if candidate.get("answers") and not response_data.get("answers"):
                response_data = candidate
            elif raw_results and not result_lists:
                response_data = candidate

            if not raw_results and not candidate.get("answers"):
                continue

            selected_engines.append(engine_name)
            if raw_results:
                result_lists.append(raw_results)

            # Fast mode always stops at the first usable engine. Balanced mode
            # stops early when the first result set is already relevant/diverse;
            # otherwise it obtains one additional free source and fuses results.
            if body.mode == "fast" or body.engines:
                break
            if body.mode == "balanced" and len(result_lists) == 1:
                if result_quality(body.q, raw_results[:max_results]) >= BALANCED_QUALITY_THRESHOLD:
                    break
            if len(result_lists) >= target_lists:
                break

    if not result_lists and not response_data.get("answers") and last_error and not skipped_engines:
        raise HTTPException(status_code=502, detail=f"SearXNG search failed: {last_error}")

    if len(result_lists) == 1:
        results = result_lists[0][:max_results]
    else:
        results = fuse_results(result_lists, max_results)

    output = {
        "query": response_data.get("query", body.q),
        "results": results,
        "answers": response_data.get("answers", []),
        "suggestions": response_data.get("suggestions", []),
        "mode": body.mode,
        "quality_score": result_quality(body.q, results),
        "selected_engine": selected_engines[0] if len(selected_engines) == 1 else None,
        "selected_engines": selected_engines,
        "attempted_engines": attempted_engines,
        "skipped_engines": skipped_engines,
        "unresponsive_engines": diagnostics,
        "cache_hit": False,
    }
    if SEARCH_CACHE_TTL_SECONDS > 0 and (results or output["answers"]):
        SEARCH_CACHE[key] = (now, copy.deepcopy(output))
    return output


async def fetch_url(url: str, max_chars: int):
    try:
        async with httpx.AsyncClient(
            timeout=30,
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT},
        ) as client:
            response = await client.get(url)
            response.raise_for_status()
            html = response.text
    except Exception as error:
        raise HTTPException(status_code=502, detail=f"Fetch failed: {error}")

    text = trafilatura.extract(
        html,
        url=url,
        include_comments=False,
        include_tables=False,
    ) or ""

    soup = BeautifulSoup(html, "lxml")
    title = soup.title.string.strip() if soup.title and soup.title.string else None
    if not text:
        text = soup.get_text("\n", strip=True)

    max_chars = max(100, min(max_chars, 200000))
    return {
        "url": str(response.url),
        "title": title,
        "text": text[:max_chars],
        "chars": min(len(text), max_chars),
        "truncated": len(text) > max_chars,
    }


@app.get("/health")
async def health():
    now = time.monotonic()
    active_cooldowns = {
        engine: {"remaining_seconds": max(0, round(until - now)), "reason": reason}
        for engine, (until, reason) in ENGINE_COOLDOWNS.items()
        if until > now
    }
    return {
        "ok": True,
        "searxng": SEARXNG_URL,
        "default_engines": SEARXNG_DEFAULT_ENGINES,
        "cache_entries": len(SEARCH_CACHE),
        "engine_cooldowns": active_cooldowns,
    }


@app.get("/websearch")
async def websearch_get(
    request: Request,
    q: str,
    max_results: int = 10,
    pageno: int = 1,
    language: str = "auto",
    categories: Optional[str] = None,
    engines: Optional[str] = None,
    time_range: Optional[str] = None,
    mode: Literal["fast", "balanced", "deep"] = "balanced",
):
    require_api_key(request)
    return await do_search(
        SearchBody(
            q=q,
            max_results=max_results,
            pageno=pageno,
            language=language,
            categories=categories,
            engines=engines,
            time_range=time_range,
            mode=mode,
        ),
        request,
    )


@app.post("/websearch")
async def websearch_post(body: SearchBody, request: Request):
    require_api_key(request)
    return await do_search(body, request)


@app.get("/webfetch")
async def webfetch_get(request: Request, url: HttpUrl = Query(...), max_chars: int = 20000):
    require_api_key(request)
    return await fetch_url(str(url), max_chars)


@app.post("/webfetch")
async def webfetch_post(body: FetchBody, request: Request):
    require_api_key(request)
    return await fetch_url(str(body.url), body.max_chars)


# Compatibility aliases using underscored names.
@app.get("/api/web_search")
async def api_web_search_get(
    request: Request,
    q: str,
    max_results: int = 10,
    pageno: int = 1,
    language: str = "auto",
    categories: Optional[str] = None,
    engines: Optional[str] = None,
    time_range: Optional[str] = None,
    mode: Literal["fast", "balanced", "deep"] = "balanced",
):
    return await websearch_get(request, q, max_results, pageno, language, categories, engines, time_range, mode)


@app.post("/api/web_search")
async def api_web_search_post(body: SearchBody, request: Request):
    require_api_key(request)
    return await do_search(body, request)


@app.get("/api/web_fetch")
async def api_web_fetch_get(request: Request, url: HttpUrl = Query(...), max_chars: int = 20000):
    require_api_key(request)
    return await fetch_url(str(url), max_chars)


@app.post("/api/web_fetch")
async def api_web_fetch_post(body: FetchBody, request: Request):
    require_api_key(request)
    return await fetch_url(str(body.url), body.max_chars)
