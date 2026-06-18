#!/usr/bin/env bash
# Safe push script — always pull latest brain data before pushing code changes.
# Usage: bash push.sh "your commit message"
# This ensures Actions-committed brain/ data is never overwritten.

set -e

MSG="${1:-chore: update}"

echo "=== Pulling latest (includes any brain data committed by Actions) ==="
git pull --rebase origin main

echo "=== Staging your changes (never touches brain/) ==="
# Stage everything EXCEPT brain/ (brain is Actions-only data)
git add agent/ docs/ .github/ requirements.txt push.sh
# Also stage any root-level files if changed
git add -u

echo "=== Committing ==="
git commit -m "$MSG" 2>/dev/null || echo "(nothing new to commit)"

echo "=== Pushing ==="
git push origin main

echo "=== Done. Brain data is safe. ==="
