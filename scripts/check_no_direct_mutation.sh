#!/usr/bin/env bash
# Migration gate (M3): zero direct entity status mutations outside terminal tools.
set -euo pipefail

results=$(grep -rnP '\w+\.status\s*=\s' --include='*.py' app/ 2>/dev/null | grep -v '# terminal-tool' || true)

if [ -n "$results" ]; then
  echo "FAIL: Direct status mutations found (only terminal tools may write status):"
  echo "$results"
  exit 1
fi

echo "OK: No direct status mutations detected."
exit 0
