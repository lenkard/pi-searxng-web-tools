from fastapi import FastAPI, HTTPException, Query, Request
from pydantic import BaseModel, HttpUrl
from typing import Any, Literal, Optional
from contextlib import asynccontextmanager
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from pathlib import Path
import asyncio
import copy
import ipaddress
import json
import logging
import os
import re
import secrets
import socket
import time

import anyio
import httpx
import trafilatura
import yaml
from bs4 import BeautifulSoup

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
log = logging.getLogger("webfetch-api")

SEARXNG_URL = os.getenv("SEARXNG_URL", "http://searxng:8080/search")
SEARXNG_DEFAULT_ENGINES = os.getenv("SEARXNG_DEFAULT_ENGINES", "bing,yep,mwmbl,wiby").strip()
SEARCH_CACHE_TTL_SECONDS = max(0, int(os.getenv("SEARCH_CACHE_TTL_SECONDS", "900")))
ENGINE_COOLDOWN_SECONDS = max(10, int(os.getenv("ENGINE_COOLDOWN_SECONDS", "300")))
BALANCED_QUALITY_THRESHOLD = min(1.0, max(0.0, float(os.getenv("BALANCED_QUALITY_THRESHOLD", "0.58"))))
USER_AGENT = os.getenv("WEB_API_USER_AGENT", "Mozilla/5.0 (compatible; private-web-api/1.0)")
WEB_API_KEY = os.getenv("WEB_API_KEY", "")

# --- SSRF protection for web_fetch -------------------------------------------
# All four webfetch endpoints route through fetch_url(), so the guard lives here
# once. Refuse hosts that resolve to private / loopback / link-local / reserved
# ranges (incl. cloud metadata endpoints). Explicit escape hatch for trusted
# deployments that must fetch internal addresses.
WEB_FETCH_ALLOW_PRIVATE = os.getenv("WEB_FETCH_ALLOW_PRIVATE", "false").lower() in ("1", "true", "yes")

def _is_blocked_ip(addr: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    # IPv4-mapped IPv6 (::ffff:a.b.c.d) is a classic bypass; check the v4 form.
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped is not None:
        addr = addr.ipv4_mapped
    return not addr.is_global or addr.is_multicast


def assert_safe_host(host: str) -> None:
    """Reject hosts that resolve to a private or reserved address."""
    if WEB_FETCH_ALLOW_PRIVATE:
        return
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as error:
        raise HTTPException(status_code=400, detail=f"Could not resolve host {host}: {error}")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if _is_blocked_ip(ip):
            raise HTTPException(
                status_code=400,
                detail=f"Refused: {host} resolves to a private or reserved address",
            )


async def _ssrf_request_hook(request: httpx.Request) -> None:
    # Fires on the initial request and on every redirect hop, so a redirect to an
    # internal address is still caught. ponytail: this covers direct private IPs,
    # metadata endpoints, and redirects. NOT covered: DNS rebinding (TTL-0 returns
    # a public IP at check time, private at connect time). Pinning the resolved IP
    # at the transport layer would close that; out of scope for a low-risk private
    # search endpoint.
    host = request.url.host
    if host:
        await anyio.to_thread.run_sync(assert_safe_host, host)


DEFAULT_ALLOWED_ENGINES = """
google cse,mojeek,yep,bing,mwmbl,wiby,wikipedia,github,arxiv,
crossref,gitlab,github code,hackernews,openalex,stackoverflow,semantic scholar,
cse people,cse instagram threads,cse facebook,cse social,cse social vortimo,
cse reddit,cse tiktok,cse iran social,cse linkedin uk,cse australia jobs,
cse headhunter,cse fbi wanted,cse ofac sanctions,cse documents,cse documents 2,
cse documents 3,cse russian courts,cse russian courts filtered,
cse central asia stats,cse baltic stats,cse wikileaks,cse fact checks,
cse file sharing,cse amazon cloud,cse google drive,cse slideshare,cse webcams,
cse telegram,cse slack discord
"""
SEARXNG_SETTINGS_PATH = os.getenv("SEARXNG_SETTINGS_PATH", "/etc/searxng/settings.yml")


def _load_allowed_from_settings(path: str = SEARXNG_SETTINGS_PATH) -> Optional[set]:
    """Derive the engine allowlist from SearXNG settings.yml (keep_only + custom engines).

    settings.yml is the single source of truth for which engines exist; the API
    reads it so the two services never drift. Returns None when the file is
    absent or unparseable so callers fall back to the built-in default list.
    """
    try:
        data = yaml.safe_load(Path(path).read_text())
    except FileNotFoundError:
        return None
    except Exception as error:
        log.warning("could not parse %s: %s; using built-in allowlist", path, error)
        return None
    engines: set[str] = set()
    use_default = (data or {}).get("use_default_settings") or {}
    keep_only = (use_default.get("engines") or {}).get("keep_only") or []
    engines.update(str(name) for name in keep_only)
    for entry in (data or {}).get("engines") or []:
        name = entry.get("name") if isinstance(entry, dict) else None
        if name:
            engines.add(str(name))
    return engines


# Explicit env override wins; otherwise derive from settings.yml (single source
# of truth); otherwise the built-in default list (fallback for non-compose use).
_env_allowed = os.getenv("SEARXNG_ALLOWED_ENGINES")
if _env_allowed:
    ALLOWED_ENGINES = {engine.strip() for engine in _env_allowed.split(",") if engine.strip()}
else:
    ALLOWED_ENGINES = _load_allowed_from_settings() or {
        engine.strip()
        for engine in DEFAULT_ALLOWED_ENGINES.split(",")
        if engine.strip()
    }

SEARCH_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
ENGINE_COOLDOWNS: dict[str, tuple[float, str]] = {}

# --- Engine health monitoring ------------------------------------------------
# A background task periodically probes each engine through SearXNG and records
# whether it responds. Chronically broken engines are auto-excluded from
# default searches so single-engine failures stop degrading results silently.
# State persists to disk so restarts keep failure history. ponytail: one file,
# in-process loop, no DB/HTML dashboard; this is an agent-facing tool, so the
# JSON endpoints ARE the dashboard.
WEBFETCH_DATA_DIR = os.getenv("WEBFETCH_DATA_DIR", "/data")
PROBE_QUERY = os.getenv("PROBE_QUERY", "open source software")
PROBE_INTERVAL_GENERAL = max(60, int(os.getenv("PROBE_INTERVAL_GENERAL", "900")))
PROBE_INTERVAL_CSE = max(300, int(os.getenv("PROBE_INTERVAL_CSE", "7200")))
PROBE_TICK_SECONDS = max(30, int(os.getenv("PROBE_TICK_SECONDS", "60")))
PROBE_STARTUP_DELAY = max(0, int(os.getenv("PROBE_STARTUP_DELAY", "30")))
ENGINE_BROKEN_THRESHOLD = max(1, int(os.getenv("ENGINE_BROKEN_THRESHOLD", "3")))

ENGINE_HEALTH: dict[str, dict[str, Any]] = {}


def _health_path() -> Path:
    return Path(WEBFETCH_DATA_DIR) / "engine_health.json"


def load_health() -> None:
    try:
        data = json.loads(_health_path().read_text())
        if isinstance(data, dict):
            ENGINE_HEALTH.update(data)
        log.info("loaded engine health state for %d engines", len(ENGINE_HEALTH))
    except FileNotFoundError:
        pass
    except Exception as error:
        log.warning("could not load engine health state: %s", error)


def save_health() -> None:
    try:
        path = _health_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(ENGINE_HEALTH))
        tmp.replace(path)
    except Exception as error:
        log.warning("could not save engine health state: %s", error)


def is_google_cse(engine: str) -> bool:
    return engine == "google cse" or engine.startswith("cse ")


def broken_engines() -> list[str]:
    return sorted(
        name for name, state in ENGINE_HEALTH.items()
        if name in ALLOWED_ENGINES and state.get("status") == "broken"
    )


def _is_temporarily_unavailable(engine: str, now: Optional[float] = None) -> bool:
    state = ENGINE_HEALTH.get(engine) or {}
    if state.get("status") == "broken":
        return True
    if state.get("status") == "rate_limited" and float(state.get("retry_after") or 0) > (now or time.time()):
        return True
    group_cooldown = ENGINE_COOLDOWNS.get("google cse") if is_google_cse(engine) else None
    return bool(group_cooldown and group_cooldown[0] > time.monotonic())


def _available_engines(engines: list[str]) -> tuple[list[str], list[str]]:
    unavailable = [engine for engine in engines if _is_temporarily_unavailable(engine)]
    return [engine for engine in engines if engine not in unavailable], unavailable


def _unresponsive_reason(engine: str, unresponsive: list[Any]) -> Optional[str]:
    for item in unresponsive or []:
        if isinstance(item, (list, tuple)) and item and str(item[0]) == engine:
            return str(item[1]) if len(item) > 1 else "unresponsive"
    return None


def _record_probe(engine: str, failed: bool, reason: Optional[str], latency_ms: Optional[int]) -> None:
    now = time.time()
    state = ENGINE_HEALTH.get(engine) or {
        "status": "unknown", "last_check": 0, "last_success": 0,
        "latency_ms": None, "consecutive_failures": 0, "last_error": None,
    }
    ENGINE_HEALTH[engine] = state
    state["last_check"] = now
    if not failed:
        previous = state.get("status")
        state.update(status="healthy", last_success=now, latency_ms=latency_ms,
                     consecutive_failures=0, last_error=None, retry_after=None)
        if previous in ("broken", "rate_limited", "degraded", "unknown"):
            log.info("ENGINE_RECOVER engine=%s prev=%s", engine, previous)
        return
    state["last_error"] = reason
    is_rate_limit = bool(reason) and cooldown_reason([reason]) is not None
    log.warning("ENGINE_FAIL engine=%s reason=%s", engine, reason)
    if is_rate_limit:
        state["status"] = "rate_limited"
        state["retry_after"] = now + ENGINE_COOLDOWN_SECONDS
    else:
        state["consecutive_failures"] = int(state.get("consecutive_failures", 0)) + 1
    if not is_rate_limit and state["consecutive_failures"] >= ENGINE_BROKEN_THRESHOLD:
        if state.get("status") != "broken":
            log.warning("ENGINE_BROKEN engine=%s failures=%s reason=%s",
                        engine, state["consecutive_failures"], reason)
        state["status"] = "broken"
    elif not is_rate_limit:
        state["status"] = "degraded"


async def _probe_one(engine: str) -> None:
    start = time.monotonic()
    failed = False
    reason: Optional[str] = None
    latency_ms: Optional[int] = None
    try:
        async with httpx.AsyncClient(timeout=20, headers={"User-Agent": USER_AGENT}) as client:
            response = await client.get(SEARXNG_URL, params={
                "q": PROBE_QUERY, "format": "json", "engines": engine, "pageno": 1,
            })
            response.raise_for_status()
            payload = response.json()
        reason = _unresponsive_reason(engine, payload.get("unresponsive_engines", []))
        if reason:
            failed = True
        latency_ms = int((time.monotonic() - start) * 1000)
    except Exception as error:
        failed = True
        reason = f"probe error: {error}"
    _record_probe(engine, failed, reason, latency_ms)
    if failed and reason and is_google_cse(engine) and cooldown_reason([reason]):
        ENGINE_COOLDOWNS["google cse"] = (time.monotonic() + ENGINE_COOLDOWN_SECONDS, reason)


def _next_cse_probe(now: float) -> Optional[str]:
    cses = sorted(e for e in ALLOWED_ENGINES if is_google_cse(e))
    if not cses:
        return None
    cooldown = ENGINE_COOLDOWNS.get("google cse")
    if cooldown and cooldown[0] > time.monotonic():
        return None
    last_probe = max(((ENGINE_HEALTH.get(e) or {}).get("last_check", 0) for e in cses), default=0)
    if now - last_probe < PROBE_INTERVAL_CSE / len(cses):
        return None
    return min(cses, key=lambda e: (ENGINE_HEALTH.get(e) or {}).get("last_check", 0))


async def _probe_loop() -> None:
    await asyncio.sleep(PROBE_STARTUP_DELAY)
    while True:
        try:
            now = time.time()
            probed = 0
            for engine in sorted(e for e in ALLOWED_ENGINES if not is_google_cse(e)):
                last = (ENGINE_HEALTH.get(engine) or {}).get("last_check", 0)
                cooldown = ENGINE_COOLDOWNS.get(engine)
                if now - last < PROBE_INTERVAL_GENERAL or (cooldown and cooldown[0] > time.monotonic()):
                    continue
                await _probe_one(engine)
                probed += 1

            # CSEs share one Google-facing residential IP. Rotate one engine
            # across the full interval instead of repeating a burst sweep.
            cse = _next_cse_probe(now)
            if cse:
                await _probe_one(cse)
                probed += 1

            if probed:
                log.info("PROBE checked=%d engines", probed)
                save_health()
        except asyncio.CancelledError:
            raise
        except Exception as error:
            log.exception("probe loop error: %s", error)
        await asyncio.sleep(PROBE_TICK_SECONDS)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    load_health()
    task = asyncio.create_task(_probe_loop())
    log.info("STARTUP engine-health probe loop started")
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        save_health()


app = FastAPI(title="Private Web Search/Fetch API", version="1.5.3", lifespan=lifespan)


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


# Keyword heuristics for engines=auto. ponytail: naive substring rules, first
# match wins, tunable via AUTO_ROUTES. No ML, no new dependency. All engines
# referenced here must exist in ALLOWED_ENGINES.
AUTO_ROUTES: list[tuple[tuple[str, ...], str]] = [
    (("reddit", "opinion", "opinions", "review", "reviews", "experience",
      "experiences", "forum", "discussion", "comments"),
     "cse reddit"),
    (("paper", "papers", "citation", "citations", "study", "studies", "academic",
      "research", "arxiv", "doi", "scholar", "journal", "preprint"),
     "openalex,semantic scholar,crossref,arxiv"),
    (("error", "exception", "stacktrace", "stack trace", "traceback", "segfault",
      "crash", "undefined", "nullpointer"),
     "stackoverflow,github code"),
    (("github", "gitlab", "repository", "repo", "implementation", "source code",
      "library", "package", "npm", "pypi"),
     "github code,github,gitlab"),
    (("pdf", "report", "manual", "presentation", "slides", "whitepaper",
      "datasheet", "documentation"),
     "cse documents"),
    (("twitter", "x.com", "instagram", "tiktok", "linkedin", "facebook",
      "mastodon", "social"),
     "cse social"),
]


def route_auto(query: str) -> Optional[str]:
    """Pick an engine set for a query via keyword heuristics, or None for the default chain."""
    q = query.lower()
    for keywords, engines in AUTO_ROUTES:
        if any(keyword in q for keyword in keywords):
            return engines
    return None


def cache_key(body: SearchBody) -> str:
    return body.model_dump_json(exclude_none=True)


async def do_search(body: SearchBody, request: Request):
    body.q = body.q.strip()
    if not body.q:
        raise HTTPException(status_code=400, detail="Search query cannot be empty")
    if body.pageno < 1:
        raise HTTPException(status_code=400, detail="pageno must be at least 1")
    if body.max_results < 1 or body.max_results > 50:
        raise HTTPException(status_code=400, detail="max_results must be between 1 and 50")

    key = cache_key(body)
    explicit_engines = body.engines not in (None, "auto")

    # Auto routes are hints, not dead ends: unavailable focused engines are
    # filtered and the safe default chain remains available as fallback.
    auto_routed = False
    auto_excluded: list[str] = []
    if body.engines == "auto":
        routed = route_auto(body.q)
        if routed:
            wanted = [e.strip() for e in routed.split(",") if e.strip()]
            kept, auto_excluded = _available_engines(wanted)
            body.engines = ",".join(kept) if kept else None
            auto_routed = bool(kept)
        else:
            body.engines = None

    if body.engines:
        requested_engines = {engine.strip() for engine in body.engines.split(",") if engine.strip()}
        unknown_engines = sorted(requested_engines - ALLOWED_ENGINES)
        if unknown_engines:
            raise HTTPException(status_code=400, detail=f"Unknown or disabled engines: {', '.join(unknown_engines)}")

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
    # Defaults are a sequential fallback chain; auto routes try their focused
    # engines first and then fall back to that chain.
    full_chain = [engine.strip() for engine in SEARXNG_DEFAULT_ENGINES.split(",") if engine.strip()]
    available_defaults, default_excluded = _available_engines(full_chain)
    excluded_unavailable = list(dict.fromkeys(auto_excluded + default_excluded))
    excluded_broken = [
        engine for engine in excluded_unavailable
        if (ENGINE_HEALTH.get(engine) or {}).get("status") == "broken"
    ]
    if explicit_engines:
        engine_attempts = [body.engines]
    else:
        engine_attempts = ([body.engines] if auto_routed and body.engines else []) + [
            engine for engine in available_defaults if engine != body.engines
        ]

    max_results = max(1, min(body.max_results, 50))
    target_lists = 1 if explicit_engines or body.mode == "fast" else (2 if body.mode == "balanced" else 3)
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
            names = [name.strip() for name in engine_name.split(",") if name.strip()]
            cooldown_key = "google cse" if names and all(is_google_cse(name) for name in names) else engine_name
            cooldown = ENGINE_COOLDOWNS.get(cooldown_key)
            if cooldown and cooldown[0] > now:
                skipped_engines.append(engine_name)
                diagnostics.append([engine_name, f"local cooldown: {cooldown[1]}"])
                continue
            if cooldown:
                ENGINE_COOLDOWNS.pop(cooldown_key, None)

            params = dict(base_params)
            if engine:
                params["engines"] = engine
            attempted_engines.append(engine_name)
            started = time.monotonic()
            try:
                response = await client.get(SEARXNG_URL, params=params)
                response.raise_for_status()
                candidate = response.json()
            except Exception as error:
                last_error = error
                diagnostics.append([engine_name, f"request failed: {error}"])
                ENGINE_COOLDOWNS[cooldown_key] = (now + min(ENGINE_COOLDOWN_SECONDS, 60), "request failure")
                continue

            latency_ms = int((time.monotonic() - started) * 1000)
            candidate_diagnostics = candidate.get("unresponsive_engines", [])
            diagnostics.extend(candidate_diagnostics)
            reason = cooldown_reason(candidate_diagnostics)
            if reason:
                ENGINE_COOLDOWNS[cooldown_key] = (now + ENGINE_COOLDOWN_SECONDS, reason)
            for name in names:
                engine_reason = _unresponsive_reason(name, candidate_diagnostics)
                _record_probe(name, bool(engine_reason), engine_reason, latency_ms)
                if engine_reason and is_google_cse(name) and cooldown_reason([engine_reason]):
                    ENGINE_COOLDOWNS["google cse"] = (now + ENGINE_COOLDOWN_SECONDS, engine_reason)
            if names:
                save_health()

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

            # Some knowledge engines (notably Wikipedia) return only an
            # infobox. Convert it to a normal result so agent callers do not
            # receive a misleading empty response.
            if not raw_results:
                for infobox in candidate.get("infoboxes", []):
                    urls = infobox.get("urls") or []
                    url = infobox.get("id") or (urls[0].get("url") if urls else None)
                    if not url:
                        continue
                    raw_results.append(
                        {
                            "title": infobox.get("infobox") or infobox.get("title"),
                            "url": url,
                            "content": infobox.get("content"),
                            "engine": infobox.get("engine"),
                            "score": 1.0,
                            "publishedDate": None,
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
            if body.mode == "fast" or explicit_engines:
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

    current_broken = broken_engines()
    degraded = bool(diagnostics) or bool(excluded_unavailable) or bool(skipped_engines)
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
        "broken_engines": current_broken,
        "excluded_broken": excluded_broken,
        "excluded_unavailable": excluded_unavailable,
        "degraded": degraded,
        "cache_hit": False,
    }
    if excluded_unavailable:
        log.info("SEARCH_EXCLUDE_UNAVAILABLE query=%r engines=%s", body.q, excluded_unavailable)
    if degraded:
        log.info("SEARCH_DEGRADED query=%r diagnostics=%s", body.q, diagnostics)
    if SEARCH_CACHE_TTL_SECONDS > 0 and (results or output["answers"]):
        SEARCH_CACHE[key] = (now, copy.deepcopy(output))
    return output


async def fetch_url(url: str, max_chars: int):
    try:
        async with httpx.AsyncClient(
            timeout=30,
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT},
            event_hooks={"request": [_ssrf_request_hook]},
        ) as client:
            response = await client.get(url)
            response.raise_for_status()
            html = response.text
    except HTTPException:
        # Let validation/SSRF errors (400) propagate; do not mask them as 502.
        raise
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


def _effective_status(name: str) -> str:
    state = ENGINE_HEALTH.get(name) or {}
    status = state.get("status", "unknown")
    if status == "rate_limited" and float(state.get("retry_after") or 0) <= time.time():
        return "degraded"
    return status


def _engine_view(name: str) -> dict[str, Any]:
    state = ENGINE_HEALTH.get(name) or {}
    return {
        "name": name,
        "category": "cse" if is_google_cse(name) else "general",
        "status": _effective_status(name),
        "last_check": state.get("last_check"),
        "last_success": state.get("last_success"),
        "latency_ms": state.get("latency_ms"),
        "consecutive_failures": state.get("consecutive_failures", 0),
        "last_error": state.get("last_error"),
        "retry_after": state.get("retry_after"),
    }


def _health_summary() -> dict[str, int]:
    counts: dict[str, int] = {}
    for name in ALLOWED_ENGINES:
        status = _effective_status(name)
        counts[status] = counts.get(status, 0) + 1
    return counts


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
        "engine_health": _health_summary(),
        "broken_engines": broken_engines(),
    }


@app.get("/engines")
async def engines():
    return {"engines": [_engine_view(name) for name in sorted(ALLOWED_ENGINES)]}


@app.get("/engines/health")
async def engines_health():
    views = [_engine_view(name) for name in sorted(ALLOWED_ENGINES)]
    return {
        "summary": _health_summary(),
        "broken": [v["name"] for v in views if v["status"] == "broken"],
        "rate_limited": [v["name"] for v in views if v["status"] == "rate_limited"],
        "engines": views,
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


if __name__ == "__main__":
    # SSRF guard self-check (runnable via `python app.py`, no network needed:
    # IP literals skip DNS).
    import sys

    for ip_s in ["169.254.169.254", "127.0.0.1", "10.1.2.3", "172.16.0.1",
                 "192.168.0.1", "100.64.0.1", "0.0.0.1", "224.0.0.1"]:
        assert _is_blocked_ip(ipaddress.ip_address(ip_s)), f"expected blocked: {ip_s}"
    assert _is_blocked_ip(ipaddress.ip_address("::ffff:169.254.169.254")), "mapped-v6 bypass"
    assert _is_blocked_ip(ipaddress.ip_address("::1")), "::1 should be blocked"
    for ip_s in ["1.1.1.1", "8.8.8.8", "93.184.216.34"]:
        assert not _is_blocked_ip(ipaddress.ip_address(ip_s)), f"expected allowed: {ip_s}"
    for host in ["169.254.169.254", "127.0.0.1"]:
        try:
            assert_safe_host(host)
        except HTTPException as exc:
            assert exc.status_code == 400, host
        else:
            raise SystemExit(f"FAIL: {host} was not blocked")

    # Engine-health classification: N consecutive failures -> broken, then resets on success.
    ENGINE_HEALTH.clear()
    probe_engine = "__selftest_engine__"
    for _ in range(ENGINE_BROKEN_THRESHOLD):
        _record_probe(probe_engine, True, "probe error: simulated", None)
    assert ENGINE_HEALTH[probe_engine]["status"] == "broken", ENGINE_HEALTH[probe_engine]
    _record_probe(probe_engine, False, None, 123)
    assert ENGINE_HEALTH[probe_engine]["status"] == "healthy", ENGINE_HEALTH[probe_engine]
    assert ENGINE_HEALTH[probe_engine]["consecutive_failures"] == 0
    # A rate-limit reason must classify as rate_limited, not broken.
    _record_probe(probe_engine, True, "HTTP 429: too many requests", None)
    assert ENGINE_HEALTH[probe_engine]["status"] == "rate_limited", ENGINE_HEALTH[probe_engine]
    ENGINE_HEALTH.clear()

    # auto keyword routing
    assert route_auto("python stack trace crash") == "stackoverflow,github code"
    assert route_auto("climate change paper citation doi") == "openalex,semantic scholar,crossref,arxiv"
    assert route_auto("best laptop reddit opinions") == "cse reddit"
    assert route_auto("deploy django github repository") == "github code,github,gitlab"
    assert route_auto("hello world foo bar") is None  # no rule -> default chain

    # settings.yml -> allowlist derivation (single source of truth)
    import tempfile
    sample_settings = (
        "use_default_settings:\n"
        "  engines:\n"
        "    keep_only:\n"
        "      - google cse\n"
        "      - bing\n"
        "engines:\n"
        "  - name: cse reddit\n"
        "    engine: google_cse\n"
        "  - name: cse documents\n"
        "    engine: google_cse\n"
    )
    with tempfile.NamedTemporaryFile("w", suffix=".yml", delete=False) as tmp:
        tmp.write(sample_settings)
        tmp_path = tmp.name
    derived = _load_allowed_from_settings(tmp_path)
    os.unlink(tmp_path)
    assert derived == {"google cse", "bing", "cse reddit", "cse documents"}, derived

    print("self-check OK")
    sys.exit(0)
