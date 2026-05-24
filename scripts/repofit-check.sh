#!/usr/bin/env bash
# Run the repofit agent-fitness audit with the project's venv on PATH so
# .clean probes (ruff, pytest, mypy, python -m build) actually find the
# tools they want to invoke.
#
# Usage:
#   bash scripts/repofit-check.sh                       # quick run, static + derived
#   bash scripts/repofit-check.sh --include executed    # run the executed tier too
#   bash scripts/repofit-check.sh --html out.html       # write a self-contained report
set -euo pipefail

cd "$(dirname "$0")/.."

if [[ ! -d .venv ]]; then
  echo "scripts/repofit-check.sh: .venv missing — run 'python -m venv .venv && .venv/bin/pip install -e \".[dev]\"' first" >&2
  exit 1
fi

# shellcheck disable=SC1091
source .venv/bin/activate

REPOFIT_BIN="${REPOFIT_BIN:-/Users/ewi/repos/repofit/packages/engine/dist/cli/index.js}"

if [[ ! -f "$REPOFIT_BIN" ]]; then
  echo "scripts/repofit-check.sh: repofit CLI not found at $REPOFIT_BIN" >&2
  echo "  set REPOFIT_BIN=/path/to/repofit/packages/engine/dist/cli/index.js" >&2
  exit 1
fi

exec node "$REPOFIT_BIN" check "$@"
