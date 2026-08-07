#!/usr/bin/env python3
"""
Ingest a Harbor task zip into tasks/<name>/.

Usage:
    python scripts/ingest_zip.py incoming/foo.zip
    python scripts/ingest_zip.py incoming/foo.zip --name my-task
"""

from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

sys.dont_write_bytecode = True
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.common import (  # noqa: E402
    INCOMING_DIR,
    TASKS_DIR,
    ensure_python,
    find_harbor_root,
    resolve_repo_path,
    slugify_task_name,
)


def extract_zip(zip_path: Path, dest: Path) -> Path:
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "r") as zf:
        zf.extractall(dest)
    # Strip macOS junk
    macosx = dest / "__MACOSX"
    if macosx.exists():
        shutil.rmtree(macosx, ignore_errors=True)
    return find_harbor_root(dest)


def ingest(zip_path: Path, *, name: str | None = None, force: bool = False) -> Path:
    zip_path = resolve_repo_path(zip_path)
    if not zip_path.is_file():
        raise FileNotFoundError(f"zip not found: {zip_path}")

    task_name = slugify_task_name(name or zip_path.stem)
    target = TASKS_DIR / task_name
    if target.exists() and not force:
        raise FileExistsError(f"{target} already exists (pass --force to replace)")

    with tempfile.TemporaryDirectory(prefix="sentinel-ingest-") as tmp:
        tmp_path = Path(tmp)
        harbor_root = extract_zip(zip_path, tmp_path / "extract")
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(harbor_root, target)

    return target


def main() -> int:
    ensure_python()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "zip_path",
        nargs="?",
        help="Path to task zip (default: process all zips in incoming/)",
    )
    parser.add_argument("--name", help="Override tasks/<name>")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    if args.zip_path:
        paths = [resolve_repo_path(args.zip_path)]
    else:
        paths = sorted(INCOMING_DIR.glob("*.zip"))
        if not paths:
            print(f"no zips in {INCOMING_DIR}", file=sys.stderr)
            return 2

    rc = 0
    for path in paths:
        try:
            target = ingest(path, name=args.name if len(paths) == 1 else None, force=args.force)
            print(f"ingested {path.name} -> {target.relative_to(REPO_ROOT)}")
        except Exception as exc:  # noqa: BLE001
            print(f"FAIL {path}: {exc}", file=sys.stderr)
            rc = 1
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
