#!/usr/bin/env python3
"""Print the engine list from SearXNG settings.yml as a Markdown table.

settings.yml is the single source of truth for engines. Run this when updating
the README engine table:

    python3 scripts/list_engines.py

Requires PyYAML (``pip install pyyaml``). For the live, runtime view use the
``/engines`` endpoint instead.
"""
import sys
from pathlib import Path
from typing import Any

SETTINGS = Path(__file__).resolve().parent.parent / "searxng" / "settings.yml"


def engine_rows(data: dict[str, Any]) -> list[tuple[str, str]]:
    keep_only = (
        ((data.get("use_default_settings") or {}).get("engines") or {}).get("keep_only") or []
    )
    custom = {
        entry.get("name"): entry.get("engine")
        for entry in (data.get("engines") or [])
        if isinstance(entry, dict) and entry.get("name")
    }
    names = list(dict.fromkeys([*keep_only, *custom]))
    rows = []
    for name in names:
        engine_type = custom.get(name)
        if engine_type == "google_cse":
            label = "Google CSE"
        elif engine_type:
            label = "provider"
        else:
            label = "built-in"
        rows.append((name, label))
    return rows


def main() -> int:
    import yaml

    rows = engine_rows(yaml.safe_load(SETTINGS.read_text()) or {})
    print(f"_{len(rows)} engines_\n")
    print("| Engine | Type |")
    print("| --- | --- |")
    for name, label in rows:
        print(f"| {name} | {label} |")
    return 0


if __name__ == "__main__":
    sys.exit(main())
