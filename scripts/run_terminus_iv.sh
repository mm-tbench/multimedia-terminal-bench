#!/usr/bin/env bash
# Run MMTB tasks with Terminus-IV (image + video perception, no listen_audio).
#
# Modality ablation harness — used to isolate the marginal contribution of
# native standalone-audio perception via the Terminus-MM − Terminus-IV delta.
#
# Usage:
#   ./scripts/run_terminus_iv.sh -p datasets/mmtb-core/<task> -m <provider/model> [harbor flags...]

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

if [ -f "$REPO_ROOT/.env" ]; then
    set -a
    source "$REPO_ROOT/.env"
    set +a
fi

export PYTHONPATH="${REPO_ROOT}${PYTHONPATH:+:$PYTHONPATH}"

exec harbor run \
    --agent-import-path "mmtb_runtime.agent:TerminusIV" \
    "$@"
