#!/usr/bin/env bash
# Run MMTB tasks with Codex CLI authenticated via consumer ChatGPT OAuth (Codex Pro).
#
# Auth model:
#   ~/.codex/auth.json holds an OAuth token bundle (no API key) created by
#   running `codex login` once on the host. This script reads that file and
#   passes its contents to a Harbor agent (mmtb_runtime.agent:CodexOAuth) which
#   writes it inside the container at $CODEX_HOME/auth.json so codex-cli can
#   refresh tokens against the OAuth provider.
#
# Usage:
#   ./scripts/run_codex_cli.sh -p datasets/mmtb-core/<task> -m openai/gpt-5 [harbor flags...]
#
# Notes:
# - Local docker only. The host's ~/.codex/auth.json is not visible to Daytona
#   sandboxes, so passing --env daytona will not work for OAuth-based auth.
# - OPENAI_API_KEY is NOT required.
# - The token bundle is removed from the container at the end of each trial.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

CODEX_AUTH_FILE="${CODEX_AUTH_FILE:-$HOME/.codex/auth.json}"
if [ ! -f "$CODEX_AUTH_FILE" ]; then
    echo "Error: $CODEX_AUTH_FILE not found." >&2
    echo "Run 'codex login' once on this host to populate it." >&2
    exit 1
fi
if ! python3 -c "import json,sys; json.load(open('$CODEX_AUTH_FILE'))" >/dev/null 2>&1; then
    echo "Error: $CODEX_AUTH_FILE is not valid JSON." >&2
    exit 1
fi

# Load .env if present (does not provide auth, but Harbor may need other vars
# like model-router endpoints set via environment).
if [ -f "$REPO_ROOT/.env" ]; then
    set -a
    source "$REPO_ROOT/.env"
    set +a
fi

export CODEX_OAUTH_AUTH_JSON="$(cat "$CODEX_AUTH_FILE")"
export PYTHONPATH="$REPO_ROOT:${PYTHONPATH:-}"

exec harbor run \
    --agent-import-path mmtb_runtime.agent:CodexOAuth \
    "$@"
