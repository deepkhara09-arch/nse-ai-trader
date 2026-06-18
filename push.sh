#!/usr/bin/env bash
# Safe push script — always pull latest brain data before pushing code changes.
# Usage: bash push.sh "your commit message"

set -e

MSG="${1:-chore: update}"

echo "=== Pulling latest brain data from GitHub ==="
git pull --rebase origin main

echo ""
echo "=== Current brain state on GitHub ==="
py -c "
import sys; sys.path.insert(0, '.')
from agent.migrations import check_schema_health
h = check_schema_health()
print(f'  Schema version : {h[\"schema_version\"]} (code expects v{h[\"current_target\"]})')
print(f'  Needs migration: {h[\"needs_migration\"]}')
print(f'  Phase / Day    : {h[\"phase\"]} / day {h[\"day\"]}')
print(f'  Focus stocks   : {len(h[\"focus_stocks\"])}')
print(f'  Stocks tracked : {h[\"stocks_tracked\"]}')
print(f'  Closed trades  : {h[\"closed_trades\"]}')
print(f'  Open positions : {h[\"open_positions\"]}')
" 2>/dev/null || echo "  (brain files not yet present — first run)"

echo ""
echo "=== Staging code changes (never touches brain/) ==="
git add agent/ docs/ .github/ requirements.txt push.sh .gitattributes 2>/dev/null || true
git add -u 2>/dev/null || true

echo "=== Committing ==="
git commit -m "$MSG" 2>/dev/null || echo "(nothing new to commit)"

echo "=== Pushing ==="
git push origin main

echo ""
echo "=== Done. Brain data is safe. ==="
echo "    Next Actions run will auto-migrate if schema version changed."
