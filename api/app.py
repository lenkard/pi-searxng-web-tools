from fastapi import FastAPI, HTTPException, Query, Request
from pydantic import BaseModel, HttpUrl
from typing import Optional
import os
import secrets

import httpx
import trafilatura
from bs4 import BeautifulSoup

SEARXNG_URL = os.getenv("SEARXNG_URL", "http://searxng:8080/search")
SEARXNG_DEFAULT_ENGINES = os.getenv("SEARXNG_DEFAULT_ENGINES", "mojeek,yep,bing,mwmbl,wiby").strip()
USER_AGENT = os.getenv("WEB_API_USER_AGENT", "Mozilla/5.0 (compatible; private-web-api/1.0)")
WEB_API_KEY = os.getenv("WEB_API_KEY", "")

app = FastAPI(title="Private Web Search/Fetch API", version="1.0.0")


class SearchBody(BaseModel):
    q: str
    max_results: int = 10
    pageno: int = 1
    language: str = "auto"
    categories: Optional[str] = None
    engines: Optional[str] = None
    time_range: Optional[str] = None


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


async def do_search(body: SearchBody, request: Request):
    base_params = {
        "q": body.q,
        "format": "json",
        "pageno": body.pageno,
        "language": body.language,
    }
    for key in ("categories", "time_range"):
        value = getattr(body, key)
        if value:
            base_params[key] = value

    # Explicit caller input keeps SearXNG's normal comma-separated aggregation.
    # The configured default is instead a sequential fallback chain. This avoids
    # blocked engines poisoning a combined response and sends only one upstream
    # request in the common case where the first engine succeeds.
    engine_attempts = [body.engines] if body.engines else [
        engine.strip() for engine in SEARXNG_DEFAULT_ENGINES.split(",") if engine.strip()
    ]
    if not engine_attempts:
        engine_attempts = [None]

    data = None
    selected_engine = None
    diagnostics = []
    attempted_engines = []
    last_error = None

    async with httpx.AsyncClient(timeout=30, headers=forwarded_client_headers(request)) as client:
        for engine in engine_attempts:
            params = dict(base_params)
            if engine:
                params["engines"] = engine
                attempted_engines.append(engine)
            try:
                response = await client.get(SEARXNG_URL, params=params)
                response.raise_for_status()
                candidate = response.json()
            except Exception as error:
                last_error = error
                diagnostics.append([engine or "default", f"request failed: {error}"])
                continue

            diagnostics.extend(candidate.get("unresponsive_engines", []))
            data = candidate
            if candidate.get("results") or candidate.get("answers"):
                selected_engine = engine
                break

    if data is None:
        raise HTTPException(status_code=502, detail=f"SearXNG search failed: {last_error or 'no engine responded'}")

    max_results = max(1, min(body.max_results, 50))
    results = []
    for item in data.get("results", [])[:max_results]:
        results.append(
            {
                "title": item.get("title"),
                "url": item.get("url"),
                "content": item.get("content"),
                "engine": item.get("engine"),
                "score": item.get("score"),
                "publishedDate": item.get("publishedDate") or item.get("pubdate"),
            }
        )

    return {
        "query": data.get("query", body.q),
        "results": results,
        "answers": data.get("answers", []),
        "suggestions": data.get("suggestions", []),
        "selected_engine": selected_engine,
        "attempted_engines": attempted_engines,
        # Preserve diagnostics so an empty result set is distinguishable from
        # upstream CAPTCHA, rate-limit, timeout, and protocol failures.
        "unresponsive_engines": diagnostics,
    }


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
    return {"ok": True, "searxng": SEARXNG_URL, "default_engines": SEARXNG_DEFAULT_ENGINES}


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
):
    return await websearch_get(request, q, max_results, pageno, language, categories, engines, time_range)


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
