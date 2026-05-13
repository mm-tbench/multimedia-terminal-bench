#!/usr/bin/env bash
# Run MMTB tasks with Claude Code (installed agent, no custom perception tools).
#
# Usage:
#   ./scripts/run_claude_code.sh -p datasets/mmtb-core/<task> -m <provider/model> [harbor flags...]
#
# Examples:
#   ./scripts/run_claude_code.sh -p datasets/mmtb-core/av-desync-detection -m anthropic/claude-sonnet-4-6
#   ./scripts/run_claude_code.sh -p datasets/mmtb-core/audience-ringtone-detection -m anthropic/claude-sonnet-4-5
#
# The script:
# 1. Loads .env if present
# 2. Runs harbor with the built-in claude-code agent

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

# Load .env if present
if [ -f "$REPO_ROOT/.env" ]; then
    set -a
    source "$REPO_ROOT/.env"
    set +a
fi

exec harbor run \
    -a claude-code \
    "$@"
