#!/usr/bin/env bash
# Run MMTB tasks with Terminus-AV (audio+video perception, no view_image).
#
# Modality ablation harness — used to isolate the marginal contribution of
# native standalone-image perception via the Terminus-MM − Terminus-AV delta.
#
# Usage:
#   ./scripts/run_terminus_av.sh -p datasets/mmtb-core/<task> -m <provider/model> [harbor flags...]

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

if [ -f "$REPO_ROOT/.env" ]; then
    set -a
    source "$REPO_ROOT/.env"
    set +a
fi

export PYTHONPATH="${REPO_ROOT}${PYTHONPATH:+:$PYTHONPATH}"

exec harbor run \
    --agent-import-path "mmtb_runtime.agent:TerminusAV" \
    "$@"
