#!/usr/bin/env python3
"""Sync generated artifacts into each task's environment/ directory.

Harbor hard-codes the Docker build context to `<task>/environment/`; symlinks
that escape the context are not followed. So any file the Dockerfile needs
must physically exist inside `environment/`. This script keeps two such
artifacts in sync per task:

    scripts/fetch_media.py  →  <task>/environment/fetch_media.py   (shared)
    <task>/media.toml       →  <task>/environment/media.toml       (per-task)

Both copies are byte-identical to their source and are overwritten on every
sync. Contributors edit the source files (scripts/fetch_media.py and the
task-root media.toml); the synced copies are generated artifacts.

Runs automatically as a pre-commit hook; `validate_task_structure.py` also
checks that every copy is byte-identical to its source and blocks stale
commits.

Manual invocation:
    uv run python scripts/sync_task_env.py         # sync all tasks
    uv run python scripts/sync_task_env.py --check # exit non-zero if stale

Exits 0 on success, 1 on error or (with --check) if anything is out of sync.
"""

from __future__ import annotations

import argparse
import hashlib
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
FETCH_MEDIA_SOURCE = REPO_ROOT / "scripts" / "fetch_media.py"
TASKS_DIR = REPO_ROOT / "datasets" / "mmtb-core"


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def _iter_task_dirs():
    if not TASKS_DIR.is_dir():
        return
    for child in sorted(TASKS_DIR.iterdir()):
        if child.is_dir() and (child / "task.toml").exists():
            yield child


def _sync_pair(src: Path, dst: Path, *, check_only: bool) -> bool:
    """Return True if dst was changed (or would be, in check mode)."""
    if not src.exists():
        return False
    needs_write = not dst.exists() or _sha256(dst) != _sha256(src)
    if not needs_write:
        return False
    if check_only:
        return True
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(src.read_bytes())
    return True


def sync(check_only: bool = False) -> int:
    if not FETCH_MEDIA_SOURCE.exists():
        print(f"error: {FETCH_MEDIA_SOURCE} not found", file=sys.stderr)
        return 1

    changed: list[Path] = []

    for task_dir in _iter_task_dirs():
        pairs = [
            (FETCH_MEDIA_SOURCE, task_dir / "environment" / "fetch_media.py"),
        ]
        media_toml = task_dir / "media.toml"
        if media_toml.exists():
            pairs.append((media_toml, task_dir / "environment" / "media.toml"))

        for src, dst in pairs:
            if _sync_pair(src, dst, check_only=check_only):
                changed.append(dst)

    if not changed:
        return 0

    if check_only:
        print("error: task environment copies are out of sync:", file=sys.stderr)
        for p in changed:
            print(f"  {p.relative_to(REPO_ROOT)}", file=sys.stderr)
        print(
            "Run `uv run python scripts/sync_task_env.py` to fix.",
            file=sys.stderr,
        )
        return 1

    print(f"Synced {len(changed)} file(s):")
    for p in changed:
        print(f"  {p.relative_to(REPO_ROOT)}")

    # Re-stage updated files so the pre-commit hook's changes are in the
    # current commit (otherwise git would complain "files modified by hook").
    try:
        subprocess.run(
            ["git", "add", "--"] + [str(p) for p in changed],
            cwd=REPO_ROOT,
            check=True,
        )
    except subprocess.CalledProcessError as e:
        print(f"warning: failed to git-add synced copies: {e}", file=sys.stderr)
        return 0

    return 0


def main():
    parser = argparse.ArgumentParser(
        description="Sync fetch_media.py and media.toml into each task's environment/."
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Exit non-zero if any copy is out of sync (don't modify files)",
    )
    args = parser.parse_args()
    sys.exit(sync(check_only=args.check))


if __name__ == "__main__":
    main()
