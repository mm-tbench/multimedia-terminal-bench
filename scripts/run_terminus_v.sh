#!/usr/bin/env bash
# Run MMTB tasks with Terminus-V (video-only perception, no listen_audio, no view_image).
#
# Modality ablation harness — used to isolate the marginal contribution of
# native standalone-audio + image perception via the Terminus-MM − Terminus-V
# delta. Pairs with Terminus-A (audio-only) on the single-modality axis.
#
# Usage:
#   ./scripts/run_terminus_v.sh -p datasets/mmtb-core/<task> -m <provider/model> [harbor flags...]

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

if [ -f "$REPO_ROOT/.env" ]; then
    set -a
    source "$REPO_ROOT/.env"
    set +a
fi

export PYTHONPATH="${REPO_ROOT}${PYTHONPATH:+:$PYTHONPATH}"

exec harbor run \
    --agent-import-path "mmtb_runtime.agent:TerminusV" \
    "$@"
