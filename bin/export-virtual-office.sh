#!/usr/bin/env bash
# Human-readable Virtual Office pack (+ full conversations + program copy).
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/.." && pwd)"
export PYTHONPATH="${REPO_ROOT}${PYTHONPATH:+:$PYTHONPATH}"
exec python3 -m grok_bot_backup.virtual_office "$@"
