#!/usr/bin/env python3
"""Validate task directory structure and configuration.

Checks that a task has all required files, valid TOML config,
and a syntactically correct verifier. Fast, no Docker needed.

Usage:
    python scripts/validate_task_structure.py datasets/mmtb-core/my-task
    python scripts/validate_task_structure.py  # validates all tasks
"""

import ast
import hashlib
import sys
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
FETCH_MEDIA_SOURCE = REPO_ROOT / "scripts" / "fetch_media.py"

REQUIRED_FILES = [
    "instruction.md",
    "task.toml",
    "environment/Dockerfile",
    "environment/fetch_media.py",
    "tests/test.sh",
    "tests/verifier.py",
    "solution/solve.sh",
]


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


REQUIRED_TOML_KEYS = [
    "schema_version",
    "task.name",
    "task.description",
    "verifier.timeout_sec",
    "environment.build_timeout_sec",
]


def _resolve_key(data: dict, dotted_key: str):
    """Resolve a dotted key like 'task.name' in nested dict."""
    parts = dotted_key.split(".")
    current = data
    for part in parts:
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def validate_task(task_dir: Path) -> list[str]:
    """Returns list of error messages. Empty = valid."""
    errors = []

    if not task_dir.is_dir():
        return [f"Not a directory: {task_dir}"]

    # Check required files
    for rel_path in REQUIRED_FILES:
        full = task_dir / rel_path
        if not full.exists():
            errors.append(f"Missing: {rel_path}")
        elif full.stat().st_size == 0:
            errors.append(f"Empty file: {rel_path}")

    # Validate task.toml
    toml_path = task_dir / "task.toml"
    if toml_path.exists():
        try:
            with open(toml_path, "rb") as f:
                data = tomllib.load(f)
        except Exception as e:
            errors.append(f"Invalid TOML: {e}")
            data = None

        if data is not None:
            for key in REQUIRED_TOML_KEYS:
                if _resolve_key(data, key) is None:
                    errors.append(f"Missing TOML key: {key}")

    # Check verifier.py parses as valid Python
    verifier_path = task_dir / "tests" / "verifier.py"
    if verifier_path.exists():
        try:
            ast.parse(
                verifier_path.read_text(encoding="utf-8"),
                filename=str(verifier_path),
            )
        except SyntaxError as e:
            errors.append(f"Syntax error in verifier.py: {e}")

    # Check test.sh references verifier
    test_sh = task_dir / "tests" / "test.sh"
    if test_sh.exists():
        try:
            content = test_sh.read_text(encoding="utf-8")
            if "verifier" not in content:
                errors.append("test.sh does not reference verifier")
        except UnicodeDecodeError as e:
            errors.append(f"test.sh is not valid UTF-8: {e}")

    # Validate media.toml if present
    media_toml = task_dir / "media.toml"
    if media_toml.exists():
        try:
            with open(media_toml, "rb") as f:
                media_data = tomllib.load(f)
        except Exception as e:
            errors.append(f"Invalid media.toml: {e}")
            media_data = None

        if media_data is not None:
            entries = media_data.get("media", [])
            if not entries:
                errors.append("media.toml has no [[media]] entries")
            for i, entry in enumerate(entries):
                prefix = f"media.toml entry #{i + 1}"
                if "output" not in entry:
                    errors.append(f"{prefix}: missing 'output'")
                else:
                    out = entry["output"]
                    if out.startswith("/") or ".." in out:
                        errors.append(
                            f"{prefix}: output must be relative within assets/: {out}"
                        )
                source = entry.get("source")
                if source is None:
                    errors.append(f"{prefix}: missing [media.source]")
                else:
                    stype = source.get("type")
                    valid_types = {"url", "synthetic", "local"}
                    if stype == "youtube":
                        errors.append(
                            f'{prefix}: source.type = "youtube" is not '
                            "accepted; MMTB sources only license-free media "
                            "(url / synthetic / local). See Docs/task-authoring.md §4."
                        )
                    elif stype not in valid_types:
                        errors.append(
                            f"{prefix}: invalid source type '{stype}' (must be one of {valid_types})"
                        )
                    if stype == "url" and "url" not in source:
                        errors.append(f"{prefix}: url source missing 'url'")
                    if "license" not in source:
                        errors.append(f"{prefix}: missing 'license' in source")

    # Check Dockerfile has FROM
    dockerfile = task_dir / "environment" / "Dockerfile"
    if dockerfile.exists():
        try:
            content = dockerfile.read_text(encoding="utf-8")
            if not any(
                line.strip().startswith("FROM") for line in content.splitlines()
            ):
                errors.append("Dockerfile missing FROM instruction")
        except UnicodeDecodeError as e:
            errors.append(f"Dockerfile is not valid UTF-8: {e}")

    # environment/fetch_media.py and environment/media.toml must be
    # byte-identical to their sources. Sync is maintained by
    # scripts/sync_task_env.py (pre-commit hook).
    fetch_copy = task_dir / "environment" / "fetch_media.py"
    if fetch_copy.exists() and FETCH_MEDIA_SOURCE.exists():
        if _sha256(fetch_copy) != _sha256(FETCH_MEDIA_SOURCE):
            errors.append(
                "environment/fetch_media.py is out of sync with "
                "scripts/fetch_media.py — run `uv run python scripts/sync_task_env.py`"
            )

    media_toml_src = task_dir / "media.toml"
    media_toml_copy = task_dir / "environment" / "media.toml"
    if media_toml_src.exists() and media_toml_copy.exists():
        if _sha256(media_toml_src) != _sha256(media_toml_copy):
            errors.append(
                "environment/media.toml is out of sync with media.toml — "
                "run `uv run python scripts/sync_task_env.py`"
            )

    return errors


def find_all_tasks(base: Path) -> list[Path]:
    """Find all task directories under base."""
    tasks = []
    if not base.is_dir():
        return tasks
    for child in sorted(base.iterdir()):
        if child.is_dir() and (child / "task.toml").exists():
            tasks.append(child)
    return tasks


def main():
    args = sys.argv[1:]

    if args:
        task_dirs = [Path(a) for a in args]
    else:
        task_dirs = find_all_tasks(Path("datasets/mmtb-core"))
        if not task_dirs:
            print("No tasks found in datasets/mmtb-core/")
            sys.exit(1)

    all_ok = True

    for task_dir in task_dirs:
        errors = validate_task(task_dir)
        if errors:
            all_ok = False
            print(f"FAIL  {task_dir.name}")
            for err in errors:
                print(f"  - {err}")
        else:
            print(f"OK    {task_dir.name}")

    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
