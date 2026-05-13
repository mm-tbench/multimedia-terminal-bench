#!/usr/bin/env bash
# Download media assets from HuggingFace Hub for MMTB tasks.
#
# Usage:
#   ./scripts/download_media.sh              # Download all tasks' media
#   ./scripts/download_media.sh <task-name>  # Download media for one task
#
# Prerequisites:
#   uv tool install huggingface-hub
#
# The HF dataset stores media files mirroring the task directory structure:
#   mmtb-core/<task-id>/environment/assets/...
#
# Files are downloaded to datasets/mmtb-core/<task-id>/environment/assets/

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HF_REPO="${MMTB_HF_REPO:-mmtb-anonymous/mmtb-media}"
TASKS_DIR="$REPO_ROOT/datasets/mmtb-core"

# Detect available CLI: prefer "hf" (current HF CLI), fall back to the
# legacy "huggingface-cli" name.
if command -v hf &> /dev/null; then
    HF_CMD="hf"
elif command -v huggingface-cli &> /dev/null; then
    HF_CMD="huggingface-cli"
else
    echo "Error: neither 'hf' nor 'huggingface-cli' found. Install with: uv tool install huggingface-hub" >&2
    exit 1
fi

if [ -n "${1:-}" ]; then
    # Download media for a specific task
    TASK="$1"
    TASK_ASSETS="$TASKS_DIR/$TASK/environment/assets"
    echo "Downloading media for task: $TASK"
    mkdir -p "$TASK_ASSETS"
    "$HF_CMD" download "$HF_REPO" \
        --repo-type dataset \
        --include "mmtb-core/$TASK/environment/assets/*" \
        --local-dir "$REPO_ROOT/datasets" \
        --quiet
    echo "Done. Files at: $TASK_ASSETS"
else
    # Download all media
    echo "Downloading all media from $HF_REPO ..."
    "$HF_CMD" download "$HF_REPO" \
        --repo-type dataset \
        --local-dir "$REPO_ROOT/datasets" \
        --quiet
    echo "Done. Files at: $TASKS_DIR/*/environment/assets/"
fi
