#!/usr/bin/env python3
"""Gentle engine + CSE health diagnostic for the live SearXNG stack.

BULK-TESTING CSEs re-triggers Google's "unusual traffic" throttle on the
shared residential egress IP. So this script is passive-first: it reads the
API /health cooldown state, reports already-throttled engines WITHOUT firing,
and only queries engines that are currently healthy — CSE queries staggered
5s apart. Safe to run periodically without worsening throttling.

    python3 scripts/test_all_engines.py
"""
import sys
import time
import urllib.parse
import urllib.request
import json
from pathlib import Path
import yaml

import os
API = os.getenv("PI_WEB_API_BASE_URL", "http://172.25.0.7:8889")
SETTINGS = Path(__file__).resolve().parent.parent / "searxng" / "settings.yml"
TIMEOUT = 25
# Stagger CSE scrapers (shared Google IP) to avoid self-triggering throttling.
CSE_STAGGER = 5.0
GENERIC_STAGGER = 1.0

QUERIES = {
    "google cse": "hello world", "bing": "hello world", "mojeek": "hello world",
    "yep": "hello world", "mwmbl": "hello world", "wiby": "hello world",
    "wikipedia": "python programming language", "github": "fastapi web framework",
    "github code": "fastapi dependency injection", "arxiv": "attention transformer",
    "semantic scholar": "attention is all you need", "stackoverflow": "python list comprehension",
    "reddit": "best laptop 2024", "openalex": "climate change impacts agriculture",
    "hackernews": "startup funding", "gitlab": "ci cd pipeline yaml", "crossref": "quantum entanglement",
    "cse people": "john smith", "cse instagram threads": "travel photography",
    "cse facebook": "local business", "cse social": "social media",
    "cse social vortimo": "social media profile", "cse reddit": "mechanical keyboard",
    "cse tiktok": "cooking tutorial", "cse iran social": "telegram news",
    "cse linkedin uk": "software engineer london", "cse australia jobs": "developer sydney",
    "cse headhunter": "python developer", "cse fbi wanted": "most wanted",
    "cse ofac sanctions": "sanctions list", "cse documents": "annual report pdf",
    "cse documents 2": "research report pdf", "cse documents 3": "white paper pdf",
    "cse russian courts": "court decision", "cse russian courts filtered": "court ruling",
    "cse central asia stats": "population statistics", "cse baltic stats": "economic statistics",
    "cse wikileaks": "diplomatic cable", "cse fact checks": "fact check",
    "cse file sharing": "file sharing", "cse amazon cloud": "aws s3 documentation",
    "cse google drive": "google drive", "cse slideshare": "presentation slides",
    "cse webcams": "live webcam city", "cse telegram": "telegram channel",
    "cse slack discord": "discord server",
}


def get_json(path):
    try:
        with urllib.request.urlopen(f"{API}{path}", timeout=TIMEOUT) as r:
            return r.status, json.load(r)
    except Exception as e:
        return None, {"error": str(e)}


def search(engine, query):
    qs = urllib.parse.urlencode({"q": query, "engines": engine, "max_results": 3})
    try:
        req = urllib.request.Request(f"{API}/websearch?{qs}", headers={"User-Agent": "pi-engine-test/1.0"})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        try: body = json.load(e)
        except Exception: body = {"raw": str(e)}
        return e.code, body
    except Exception as e:
        return None, {"error": str(e)}


def is_cse(name):
    return name.startswith("cse ") or name == "google cse"


def main():
    d = yaml.safe_load(SETTINGS.read_text()) or {}
    keep = (((d.get("use_default_settings") or {}).get("engines") or {}).get("keep_only") or [])
    custom = [e["name"] for e in (d.get("engines") or []) if isinstance(e, dict) and e.get("name")]
    engines = keep + custom

    # Passive: read cooldown state first so we don't re-trigger throttling.
    _, health = get_json("/health")
    cooldowns = health.get("engine_cooldowns") if isinstance(health, dict) else None

    passed, failed, cooling = [], [], []
    print(f"Testing {len(engines)} engines against {API} (passive-first)\n{'='*68}")
    for i, eng in enumerate(engines, 1):
        if cooldowns and eng in cooldowns:
            seg = cooldowns[eng]
            tag = f"COOLDOWN ({seg.get('remaining_seconds')}s left: {seg.get('reason')})"
            cooling.append((eng, tag))
            print(f"[{i:2d}/{len(engines)}] {eng:32s} {tag}")
            continue
        q = QUERIES.get(eng, "test")
        status, data = search(eng, q)
        results = data.get("results") if isinstance(data, dict) else None
        n = len(results) if isinstance(results, list) else 0
        if status == 200 and n > 0:
            tag = f"PASS ({n} results)"; passed.append(eng)
        elif status == 200:
            tag = "FAIL (0 results)"; failed.append((eng, "0 results"))
        elif status == 400:
            tag = f"REJECTED 400 ({data.get('detail') if isinstance(data, dict) else data})"
            failed.append((eng, "rejected"))
        else:
            tag = f"ERROR status={status} {str(data)[:60]}"; failed.append((eng, f"status={status}"))
        print(f"[{i:2d}/{len(engines)}] {eng:32s} {tag}")
        time.sleep(CSE_STAGGER if is_cse(eng) else GENERIC_STAGGER)

    print("="*68)
    print(f"PASS: {len(passed)}  |  COOLDOWN: {len(cooling)}  |  FAIL: {len(failed)}  / {len(engines)}")
    if failed:
        print("\nFailures:")
        for eng, why in failed: print(f"  - {eng}: {why}")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())