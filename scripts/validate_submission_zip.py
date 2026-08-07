#!/usr/bin/env python3
"""
Validate one submission zip for Task_Ready_To_Submit.

Usage:
    python scripts/validate_submission_zip.py Task_Ready_To_Submit/<task>.zip
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
    REQUIRED_ENV_FILES,
    REQUIRED_ROOT_DIRS,
    REQUIRED_ROOT_FILES,
    REQUIRED_SOLUTION_FILES,
    REQUIRED_TEST_FILES,
    STATUS_ORDER,
    ensure_python,
    resolve_repo_path,
)

MIN_FAIL_TO_PASS = 10


def _finding(check: str, status: str, message: str, **details: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {"check": check, "status": status, "message": message}
    if details:
        payload["details"] = details
    return payload


def validate_zip(zip_path: Path) -> dict[str, Any]:
    zip_path = resolve_repo_path(zip_path)
    findings: list[dict[str, Any]] = []

    if not zip_path.is_file():
        return {
            "zip": str(zip_path),
            "status": "FAIL",
            "findings": [_finding("exists", "FAIL", "zip not found")],
        }

    try:
        zf = zipfile.ZipFile(zip_path, "r")
    except zipfile.BadZipFile as exc:
        return {
            "zip": str(zip_path),
            "status": "FAIL",
            "findings": [_finding("zip_format", "FAIL", f"bad zip: {exc}")],
        }

    with zf:
        names = [PurePosixPath(n) for n in zf.namelist() if not n.endswith("/")]
        name_set = {n.as_posix() for n in names}

        # Must be flat — no single wrapping folder
        top_levels = {n.parts[0] for n in names if n.parts}
        wrapping = False
        if len(top_levels) == 1:
            only = next(iter(top_levels))
            if only not in set(REQUIRED_ROOT_FILES) | set(REQUIRED_ROOT_DIRS):
                wrapping = True
                findings.append(
                    _finding(
                        "flat_layout",
                        "FAIL",
                        f"zip appears wrapped in folder {only!r}; must be flat Harbor root",
                    )
                )
        if not wrapping:
            findings.append(_finding("flat_layout", "PASS", "flat archive root"))

        missing = []
        for req in REQUIRED_ROOT_FILES:
            if req not in name_set:
                missing.append(req)
        for req in REQUIRED_ROOT_DIRS:
            if not any(n == req or n.startswith(req + "/") for n in name_set):
                missing.append(req + "/")
        for req in REQUIRED_ENV_FILES + REQUIRED_TEST_FILES + REQUIRED_SOLUTION_FILES:
            if req not in name_set:
                # allow legacy patch names as fail with message
                if req == "solution/golden.patch":
                    if any(x in name_set for x in ("solution/solution.patch", "solution/init_state.patch")):
                        findings.append(
                            _finding(
                                "golden_patch",
                                "FAIL",
                                "zip has legacy patch name; need solution/golden.patch",
                            )
                        )
                        continue
                missing.append(req)
        if missing:
            findings.append(_finding("required_files", "FAIL", "missing required paths", missing=missing))
        else:
            findings.append(_finding("required_files", "PASS", "required Harbor files present"))

        # Forbidden roots
        bad_roots = sorted(top_levels & FORBIDDEN_ROOT_NAMES)
        if bad_roots:
            findings.append(
                _finding("forbidden_roots", "FAIL", "forbidden root entries in zip", paths=bad_roots)
            )
        else:
            findings.append(_finding("forbidden_roots", "PASS", "no forbidden root entries"))

        # AI scaffolding / junk
        banned_hits: list[str] = []
        for n in names:
            lowered = tuple(p.lower() for p in n.parts)
            if any(p in BANNED_AI_DIRECTORY_NAMES_CI for p in lowered):
                banned_hits.append(n.as_posix())
            elif n.name.lower() in BANNED_AI_FILENAMES_CI:
                banned_hits.append(n.as_posix())
            elif any(p == "runs" for p in n.parts):
                banned_hits.append(n.as_posix())
            elif n.suffix == ".pyc" or "__pycache__" in n.parts:
                banned_hits.append(n.as_posix())
            elif len(n.parts) >= 4 and n.parts[:4] == ("environment", "repo", ".git", "logs"):
                banned_hits.append(n.as_posix())
        if banned_hits:
            findings.append(
                _finding(
                    "banned_content",
                    "FAIL",
                    "zip contains banned scaffolding/cache/runs/.git/logs",
                    sample=banned_hits[:20],
                )
            )
        else:
            findings.append(_finding("banned_content", "PASS", "no banned scaffolding in zip"))

        # instruction sync inside zip
        try:
            instr = zf.read("instruction.md").decode("utf-8")
            prob = zf.read("environment/problem_statement.md").decode("utf-8")
            if instr.replace("\r\n", "\n") == prob.replace("\r\n", "\n"):
                findings.append(_finding("instruction_sync", "PASS", "instruction sync ok in zip"))
            else:
                findings.append(
                    _finding(
                        "instruction_sync",
                        "FAIL",
                        "problem_statement.md != instruction.md inside zip",
                    )
                )
        except KeyError:
            pass  # already covered by required files

        # fail_to_pass count
        try:
            cfg = json.loads(zf.read("tests/config.json").decode("utf-8"))
            grading = cfg.get("grading") if isinstance(cfg.get("grading"), dict) else cfg
            f2p = grading.get("fail_to_pass") if isinstance(grading, dict) else None
            if not isinstance(f2p, list) or len(f2p) < MIN_FAIL_TO_PASS:
                findings.append(
                    _finding(
                        "fail_to_pass",
                        "FAIL",
                        f"fail_to_pass need ≥{MIN_FAIL_TO_PASS}",
                        count=len(f2p) if isinstance(f2p, list) else 0,
                    )
                )
            else:
                findings.append(
                    _finding("fail_to_pass", "PASS", f"fail_to_pass count {len(f2p)}", count=len(f2p))
                )
        except Exception as exc:  # noqa: BLE001
            findings.append(_finding("fail_to_pass", "FAIL", f"could not read config.json: {exc}"))

        # repo/.git present
        if any(n.as_posix().startswith("environment/repo/.git/") for n in names):
            findings.append(_finding("repo_git", "PASS", "environment/repo/.git present in zip"))
        else:
            findings.append(
                _finding("repo_git", "FAIL", "environment/repo/.git missing from zip (needed for Harbor)")
            )

    status = "PASS"
    for f in findings:
        if STATUS_ORDER.get(f["status"], 0) > STATUS_ORDER[status]:
            status = f["status"]

    return {
        "zip": str(zip_path),
        "status": status,
        "fails": sum(1 for f in findings if f["status"] == "FAIL"),
        "warns": sum(1 for f in findings if f["status"] == "WARN"),
        "findings": findings,
    }


def main() -> int:
    ensure_python()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("zip_path")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    report = validate_zip(Path(args.zip_path))
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(f"zip: {report['zip']}")
        print(f"status: {report['status']}")
        for f in report["findings"]:
            print(f"  [{f['status']}] {f['check']}: {f['message']}")
    return 0 if report["status"] != "FAIL" else 1


if __name__ == "__main__":
    raise SystemExit(main())
