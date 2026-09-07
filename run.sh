#!/usr/bin/env bash
#
# Run the humanized Greenhouse fill+submit script.
#
# Usage:
#   ./run.sh                 # uses the default test posting URL
#   ./run.sh "<job_url>"     # run against a specific Greenhouse job URL
#
set -euo pipefail

# Always run from this script's own directory so the `humanize` import resolves.
cd "$(dirname "$0")"

DEFAULT_URL="https://job-boards.greenhouse.io/greenhouse/jobs/8021661?gh_jid=8021661"
URL="${1:-$DEFAULT_URL}"

# Prefer python3; fall back to python.
PY="$(command -v python3 || command -v python || true)"
if [ -z "$PY" ]; then
    echo "[!] Python not found on PATH." >&2
    exit 1
fi

echo "[*] Running against: $URL"
exec "$PY" test_fill_submit.py "$URL"
