#!/usr/bin/env bash
# Run MMTB tasks with Terminus-A (audio-only perception — listen_audio).
#
# Usage:
#   ./scripts/run_terminus_a.sh -p datasets/mmtb-core/<task> -m <provider/model> [harbor flags...]
#
# Examples:
#   ./scripts/run_terminus_a.sh -p datasets/mmtb-core/audience-ringtone-detection -m openrouter/google/gemini-3.1-pro-preview
#   ./scripts/run_terminus_a.sh -p datasets/mmtb-core/dead-air-removal -m openrouter/google/gemini-2.5-flash
#
# Terminus-A has only listen_audio (no watch_video, no view_image).
# For video tasks, the agent must extract audio first with ffmpeg.
#
# The script:
# 1. Sets PYTHONPATH so Harbor can import mmtb_runtime.agent
# 2. Loads .env if present
# 3. Runs harbor with --agent-import-path pointing to TerminusA

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
    --agent-import-path "mmtb_runtime.agent:TerminusA" \
    "$@"
