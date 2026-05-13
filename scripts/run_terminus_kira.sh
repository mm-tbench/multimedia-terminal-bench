#!/usr/bin/env bash
# Run MMTB tasks with Terminus-KIRA (image-only perception, non-omni baseline).
#
# Usage:
#   ./scripts/run_terminus_kira.sh -p datasets/mmtb-core/<task> -m <provider/model> [harbor flags...]
#
# Examples:
#   ./scripts/run_terminus_kira.sh -p datasets/mmtb-core/av-desync-detection -m openrouter/google/gemini-3.1-pro-preview
#   ./scripts/run_terminus_kira.sh -p datasets/mmtb-core/audience-ringtone-detection -m openrouter/google/gemini-2.5-flash
#
# The script:
# 1. Sets PYTHONPATH so Harbor can import mmtb_runtime.agent
# 2. Loads .env if present
# 3. Runs harbor with --agent-import-path pointing to TerminusKira (vendored)

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

# Load .env if present
if [ -f "$REPO_ROOT/.env" ]; then
    set -a
    source "$REPO_ROOT/.env"
    set +a
fi

export PYTHONPATH="${REPO_ROOT}${PYTHONPATH:+:$PYTHONPATH}"

exec harbor run \
    --agent-import-path "mmtb_runtime.agent:TerminusKira" \
    "$@"
