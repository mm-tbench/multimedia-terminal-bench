#!/usr/bin/env python3
"""Download media assets for MMTB tasks — thin wrapper over fetch_media.py.

The per-task fetch logic lives in `scripts/fetch_media.py`. This script
iterates tasks in `datasets/mmtb-core/` (one or all) and invokes
`fetch_task()` for each. Preserves the legacy CLI so existing workflows
continue to work.

Usage:
    uv run python scripts/download_media.py                        # all tasks
    uv run python scripts/download_media.py av-desync-detection    # one task
    uv run python scripts/download_media.py --source hf            # force HF Hub
    uv run python scripts/download_media.py --source original      # force original sources
    uv run python scripts/download_media.py --dry-run              # show what would be done
    uv run python scripts/download_media.py --force                # re-download even if exists

Per-entry fallback chain is defined in fetch_media.py. Only license-free
source types (url / synthetic / local) are supported.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Add scripts/ to sys.path so we can import fetch_media.
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from fetch_media import fetch_task  # noqa: E402

TASKS_DIR = REPO_ROOT / "datasets" / "mmtb-core"


def main():
    parser = argparse.ArgumentParser(
        description="Download media assets for MMTB tasks (wrapper over fetch_media.py)."
    )
    parser.add_argument(
        "task",
        nargs="?",
        help="Task name (e.g. av-desync-detection). Omit for all tasks.",
    )
    parser.add_argument(
        "--source",
        choices=["auto", "original", "hf"],
        default="auto",
        help="Download source: auto (local -> HF -> original), "
        "hf (HF Hub only), original (source only). Default: auto.",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Show what would be downloaded"
    )
    parser.add_argument(
        "--force", action="store_true", help="Re-download even if files exist"
    )
    args = parser.parse_args()

    if args.task:
        task_dirs = [TASKS_DIR / args.task]
        if not task_dirs[0].exists():
            print(f"Error: task not found: {task_dirs[0]}", file=sys.stderr)
            sys.exit(1)
    else:
        task_dirs = sorted(
            d for d in TASKS_DIR.iterdir() if d.is_dir() and (d / "media.toml").exists()
        )

    if not task_dirs:
        print("No tasks with media.toml found.")
        sys.exit(0)

    total_ok, total_entries = 0, 0

    for task_dir in task_dirs:
        print(f"\n{'=' * 60}")
        print(f"Task: {task_dir.name}")
        print(f"{'=' * 60}")

        media_toml = task_dir / "media.toml"
        output_dir = task_dir / "environment" / "assets"
        ok, total = fetch_task(
            media_toml,
            output_dir,
            task_name=task_dir.name,
            source_mode=args.source,
            force=args.force,
            dry_run=args.dry_run,
        )
        total_ok += ok
        total_entries += total

    print(f"\n{'=' * 60}")
    print(f"Summary: {total_ok}/{total_entries} entries OK across all tasks")
    if total_ok < total_entries:
        sys.exit(1)


if __name__ == "__main__":
    main()
