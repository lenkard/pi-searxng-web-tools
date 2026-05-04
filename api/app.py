from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, HttpUrl
from typing import Optional
import os

import httpx
import trafilatura
from bs4 import BeautifulSoup

SEARXNG_URL = os.getenv("SEARXNG_URL", "http://searxng:8080/search")
USER_AGENT = os.getenv("WEB_API_USER_AGENT", "Mozilla/5.0 (compatible; private-web-api/1.0)")

app = FastAPI(title="Private Web Search/Fetch API", version="1.0.0")


class SearchBody(BaseModel):
    q: str
    max_results: int = 10
    pageno: int = 1
    language: str = "all"
    categories: Optional[str] = None
    engines: Optional[str] = None
    time_range: Optional[str] = None


class FetchBody(BaseModel):
    url: HttpUrl
    max_chars: int = 20000


async def do_search(body: SearchBody):
    params = {
        "q": body.q,
        "format": "json",
        "pageno": body.pageno,
        "language": body.language,
    }
    for key in ("categories", "engines", "time_range"):
        value = getattr(body, key)
        if value:
            params[key] = value

    try:
        async with httpx.AsyncClient(timeout=30, headers={"User-Agent": USER_AGENT}) as client:
            response = await client.get(SEARXNG_URL, params=params)
            response.raise_for_status()
            data = response.json()
    except Exception as error:
        raise HTTPException(status_code=502, detail=f"SearXNG search failed: {error}")

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
    return {"ok": True, "searxng": SEARXNG_URL}


@app.get("/websearch")
async def websearch_get(
    q: str,
    max_results: int = 10,
    pageno: int = 1,
    language: str = "all",
    categories: Optional[str] = None,
    engines: Optional[str] = None,
    time_range: Optional[str] = None,
):
    return await do_search(
        SearchBody(
            q=q,
            max_results=max_results,
            pageno=pageno,
            language=language,
            categories=categories,
            engines=engines,
            time_range=time_range,
        )
    )


@app.post("/websearch")
async def websearch_post(body: SearchBody):
    return await do_search(body)


@app.get("/webfetch")
async def webfetch_get(url: HttpUrl = Query(...), max_chars: int = 20000):
    return await fetch_url(str(url), max_chars)


@app.post("/webfetch")
async def webfetch_post(body: FetchBody):
    return await fetch_url(str(body.url), body.max_chars)


# Compatibility aliases using underscored names.
@app.get("/api/web_search")
async def api_web_search_get(
    q: str,
    max_results: int = 10,
    pageno: int = 1,
    language: str = "all",
    categories: Optional[str] = None,
    engines: Optional[str] = None,
    time_range: Optional[str] = None,
):
    return await websearch_get(q, max_results, pageno, language, categories, engines, time_range)


@app.post("/api/web_search")
async def api_web_search_post(body: SearchBody):
    return await do_search(body)


@app.get("/api/web_fetch")
async def api_web_fetch_get(url: HttpUrl = Query(...), max_chars: int = 20000):
    return await fetch_url(str(url), max_chars)


@app.post("/api/web_fetch")
async def api_web_fetch_post(body: FetchBody):
    return await fetch_url(str(body.url), body.max_chars)
