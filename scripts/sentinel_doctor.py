#!/usr/bin/env python3
"""Local environment doctor for Sentinel Ultra tooling sessions."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.common import INCOMING_DIR, READY_DIR, TASKS_DIR, ensure_python, run_cmd  # noqa: E402

MIN_PYTHON = (3, 11)
REQUIRED_COMMANDS = ("git",)
OPTIONAL_COMMANDS = ("docker", "bash", "zip")
REQUIRED_SCRIPTS = (
    "scripts/run_static_checks.py",
    "scripts/check_git_hygiene.py",
    "scripts/package_task.py",
    "scripts/validate_submission_zip.py",
    "scripts/ingest_zip.py",
    "scripts/prepare_task.py",
)


@dataclass
class Check:
    id: str
    status: str
    message: str
    details: dict[str, Any] | None = None

    def to_json(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "id": self.id,
            "status": self.status,
            "message": self.message,
        }
        if self.details:
            payload["details"] = self.details
        return payload


def check_repo_root() -> Check:
    missing = [s for s in REQUIRED_SCRIPTS if not (REPO_ROOT / s).exists()]
    for d in (INCOMING_DIR, TASKS_DIR, READY_DIR):
        if not d.is_dir():
            missing.append(str(d.relative_to(REPO_ROOT)))
    if missing:
        return Check("repo_root", "FAIL", "missing Sentinel tooling pieces", {"missing": missing})
    return Check("repo_root", "PASS", "Sentinel Ultra tooling layout looks intact")


def check_python() -> Check:
    version = sys.version_info[:3]
    if version < (*MIN_PYTHON, 0):
        return Check(
            "python",
            "FAIL",
            f"Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]}+ required; found {version[0]}.{version[1]}.{version[2]}",
        )
    return Check("python", "PASS", f"Python {version[0]}.{version[1]}.{version[2]} ok")


def check_command(name: str, *, optional: bool = False) -> Check:
    path = shutil.which(name)
    if path:
        return Check(name, "PASS", f"{name} found at {path}")
    status = "WARN" if optional else "FAIL"
    return Check(name, status, f"{name} not found on PATH")


def check_git_works() -> Check:
    result = run_cmd(["git", "--version"], timeout=10)
    if result.returncode != 0:
        return Check("git_works", "FAIL", "git --version failed", {"stderr": result.stderr.strip()})
    return Check("git_works", "PASS", result.stdout.strip() or "git ok")


def main() -> int:
    ensure_python()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    checks = [
        check_repo_root(),
        check_python(),
        *[check_command(c) for c in REQUIRED_COMMANDS],
        check_git_works(),
        *[check_command(c, optional=True) for c in OPTIONAL_COMMANDS],
    ]
    worst = "PASS"
    order = {"PASS": 0, "WARN": 1, "FAIL": 2}
    for c in checks:
        if order[c.status] > order[worst]:
            worst = c.status

    payload = {"status": worst, "checks": [c.to_json() for c in checks]}
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        print(f"status: {worst}")
        for c in checks:
            print(f"  [{c.status}] {c.id}: {c.message}")
    return 0 if worst != "FAIL" else 1


if __name__ == "__main__":
    raise SystemExit(main())
