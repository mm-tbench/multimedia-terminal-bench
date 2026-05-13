#!/usr/bin/env bash
# Upload media assets to HuggingFace Hub for an MMTB task.
#
# Usage:
#   ./scripts/upload_media.sh <task-name>
#
# Example:
#   ./scripts/upload_media.sh av-desync-detection
#
# Prerequisites:
#   uv tool install huggingface-hub
#   huggingface-cli login
#
# Uploads: datasets/mmtb-core/<task>/environment/assets/ → HF repo
#
# Policy: MMTB sources only license-free media. YouTube is not accepted
# (ToS prohibits bulk redistribution). This script inspects media.toml
# and refuses to upload any task whose entries declare
# source.type = "youtube". The enforcement stays in place as defensive
# infrastructure even though no MMTB-core task currently uses YouTube.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HF_REPO="${MMTB_HF_REPO:-mmtb-anonymous/mmtb-media}"
TASK="${1:?Usage: upload_media.sh <task-name>}"
TASK_DIR="$REPO_ROOT/datasets/mmtb-core/$TASK"
TASK_ASSETS="$TASK_DIR/environment/assets"
MEDIA_TOML="$TASK_DIR/media.toml"

# Detect available CLI: prefer "hf", fall back to "huggingface-cli"
if command -v hf &> /dev/null; then
    HF_CMD="hf"
elif command -v huggingface-cli &> /dev/null; then
    HF_CMD="huggingface-cli"
else
    echo "Error: neither 'hf' nor 'huggingface-cli' found. Install with: uv tool install huggingface-hub" >&2
    exit 1
fi

if [ ! -d "$TASK_ASSETS" ]; then
    echo "Error: no assets found at $TASK_ASSETS" >&2
    exit 1
fi

if [ ! -f "$MEDIA_TOML" ]; then
    echo "Error: no media.toml at $MEDIA_TOML — cannot verify YouTube policy" >&2
    exit 1
fi

# Refuse to upload any task whose media.toml has YouTube entries.
YT_ENTRIES=$(
    uv run python - "$MEDIA_TOML" <<'PY'
import sys, tomllib
with open(sys.argv[1], "rb") as f:
    data = tomllib.load(f)
yt = [e["output"] for e in data.get("media", []) if e.get("source", {}).get("type") == "youtube"]
for o in yt:
    print(o)
PY
)

if [ -n "$YT_ENTRIES" ]; then
    cat >&2 <<EOF
Error: media.toml contains YouTube-sourced entries:
$(echo "$YT_ENTRIES" | sed 's/^/  - /')

YouTube content must not be redistributed via our HF Hub mirror.
Contributors fetch YouTube clips via yt-dlp from original URLs at every
stage (local dev, Harbor registry, end-user).

If this task has mixed sources, the YouTube entries cannot be uploaded.
Either remove them from media.toml or restructure the task to use only
license-free sources (url / synthetic / local).
EOF
    exit 1
fi

echo "Uploading media for task: $TASK"
echo "  From: $TASK_ASSETS"
echo "  To:   $HF_REPO/mmtb-core/$TASK/environment/assets/"

"$HF_CMD" upload "$HF_REPO" \
    "$TASK_ASSETS" \
    "mmtb-core/$TASK/environment/assets" \
    --repo-type dataset

echo "Done. View at: https://huggingface.co/datasets/$HF_REPO"
