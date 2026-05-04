#!/usr/bin/env bash
set -euo pipefail

EXT_DIR="${PI_CODING_AGENT_DIR:-$HOME/.pi/agent}/extensions"
mkdir -p "$EXT_DIR"
cp "$(dirname "$0")/pi-extension/web-search-fetch.ts" "$EXT_DIR/web-search-fetch.ts"

echo "Installed pi extension to: $EXT_DIR/web-search-fetch.ts"
echo "Restart pi or run /reload inside pi."
