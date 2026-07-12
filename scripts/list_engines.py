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

import yaml

SETTINGS = Path(__file__).resolve().parent.parent / "searxng" / "settings.yml"


def main() -> int:
    data = yaml.safe_load(SETTINGS.read_text()) or {}
    keep_only = (
        ((data.get("use_default_settings") or {}).get("engines") or {}).get("keep_only") or []
    )
    custom = [
        entry.get("name")
        for entry in (data.get("engines") or [])
        if isinstance(entry, dict) and entry.get("name")
    ]
    print(f"_{len(keep_only) + len(custom)} engines_\n")
    print("| Engine | Type |")
    print("| --- | --- |")
    for name in keep_only:
        print(f"| {name} | built-in |")
    for name in custom:
        print(f"| {name} | Google CSE |")
    return 0


if __name__ == "__main__":
    sys.exit(main())
