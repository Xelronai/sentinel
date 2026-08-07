#!/usr/bin/env python3
"""
Create a flat Sentinel Ultra submission zip in Task_Ready_To_Submit/.

Excludes runs/, AI scaffolding, caches, and authoring junk.
Does not modify environment/repo tracked source.
"""

from __future__ import annotations

import argparse
import json
import sys
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

sys.dont_write_bytecode = True
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.common import (  # noqa: E402
    BANNED_AI_DIRECTORY_NAMES_CI,
    BANNED_AI_FILENAMES_CI,
    FORBIDDEN_ROOT_NAMES,
    READY_DIR,
    REQUIRED_ROOT_DIRS,
    REQUIRED_ROOT_FILES,
    ensure_python,
    resolve_task_dir,
)

try:
    from scripts import validate_submission_zip
except ImportError:  # pragma: no cover
    import validate_submission_zip  # type: ignore


ALLOWED_ROOT = set(REQUIRED_ROOT_FILES) | set(REQUIRED_ROOT_DIRS)

FORBIDDEN_ANYWHERE_FILENAMES = {
    ".gitignore",
    ".dockerignore",
    "AGENTS.md",
    "CLAUDE.md",
    "skills.md",
    "review_answer.txt",
    "metadata.json",
}


def _is_repo_git_path(parts: tuple[str, ...]) -> bool:
    return (
        len(parts) >= 3
        and parts[0] == "environment"
        and parts[1] == "repo"
        and parts[2] == ".git"
    )


def should_package(rel: PurePosixPath) -> tuple[bool, str | None]:
    parts = rel.parts
    if not parts:
        return False, "empty path"
    root = parts[0]
    if root in FORBIDDEN_ROOT_NAMES:
        return False, "forbidden root authoring path"
    if root not in ALLOWED_ROOT:
        return False, "not part of submission root"
    if any(p == "runs" for p in parts):
        return False, "runs directory"
    if any(p == "__pycache__" for p in parts) or rel.suffix == ".pyc":
        return False, "python cache"
    lowered_parts = tuple(p.lower() for p in parts)
    if any(p in BANNED_AI_DIRECTORY_NAMES_CI for p in lowered_parts):
        return False, "AI scaffolding directory"
    if parts[-1].lower() in BANNED_AI_FILENAMES_CI:
        return False, "AI scaffolding file"
    if parts[-1] in FORBIDDEN_ANYWHERE_FILENAMES:
        return False, "forbidden metadata file"
    # Allow environment/repo/.git/** except logs/
    if _is_repo_git_path(parts):
        if len(parts) >= 4 and parts[3] == "logs":
            return False, ".git/logs"
        return True, None
    if any(p.startswith(".") for p in parts):
        return False, "dotfile or dotdir metadata"
    return True, None


def iter_package_files(task_dir: Path) -> tuple[list[Path], dict[str, list[str]]]:
    included: list[Path] = []
    excluded: dict[str, list[str]] = {}
    for path in sorted(task_dir.rglob("*")):
        if not path.is_file():
            continue
        rel = PurePosixPath(path.relative_to(task_dir).as_posix())
        ok, reason = should_package(rel)
        if ok:
            included.append(path)
        else:
            excluded.setdefault(reason or "excluded", []).append(str(rel))
    return included, excluded


def package_task(
    task_dir: Path,
    *,
    out_dir: Path | None = None,
    name: str | None = None,
    validate: bool = True,
) -> dict[str, Any]:
    task_dir = resolve_task_dir(task_dir)
    out_dir = out_dir or READY_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    zip_name = f"{name or task_dir.name}.zip"
    zip_path = out_dir / zip_name

    included, excluded = iter_package_files(task_dir)
    if not included:
        raise RuntimeError("no files selected for packaging")

    if zip_path.exists():
        zip_path.unlink()

    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in included:
            arcname = path.relative_to(task_dir).as_posix()
            zf.write(path, arcname=arcname)

    result: dict[str, Any] = {
        "task_dir": str(task_dir),
        "zip": str(zip_path),
        "included_count": len(included),
        "excluded": {k: v[:20] for k, v in excluded.items()},
    }

    if validate:
        report = validate_submission_zip.validate_zip(zip_path)
        result["validation"] = report
        result["status"] = report.get("status", "FAIL")
    else:
        result["status"] = "PACKAGED"
    return result


def main() -> int:
    ensure_python()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("task_dir")
    parser.add_argument("--name", help="Output zip basename without .zip")
    parser.add_argument("--out-dir", type=Path, default=None)
    parser.add_argument("--no-validate", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    try:
        result = package_task(
            args.task_dir,
            out_dir=args.out_dir,
            name=args.name,
            validate=not args.no_validate,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"zip: {result['zip']}")
        print(f"included: {result['included_count']}")
        print(f"status: {result['status']}")
        if result.get("validation"):
            for f in result["validation"].get("findings", []):
                print(f"  [{f['status']}] {f['check']}: {f['message']}")
    return 0 if result.get("status") != "FAIL" else 1


if __name__ == "__main__":
    raise SystemExit(main())
