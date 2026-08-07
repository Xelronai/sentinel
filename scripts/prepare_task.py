#!/usr/bin/env python3
"""
Prepare a Sentinel Ultra task for submission.

Safe auto-fixes only (never edits environment/repo tracked source):
  - sync problem_statement.md from instruction.md
  - rename solution.patch / init_state.patch -> golden.patch
  - normalize CRLF on solve.sh / test.sh
  - realign base_commit_sha to HEAD (--fix-git)
  - remove .git/logs when present (--fix-git)

Then runs static + git checks and packages Task_Ready_To_Submit/<name>.zip.

Usage:
    python scripts/prepare_task.py incoming/foo.zip
    python scripts/prepare_task.py tasks/foo
    python scripts/prepare_task.py   # all zips in incoming/
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts import check_git_hygiene, ingest_zip, package_task, run_static_checks  # noqa: E402
from scripts.common import (  # noqa: E402
    INCOMING_DIR,
    LEGACY_PATCH_NAMES,
    READY_DIR,
    TASKS_DIR,
    ensure_python,
    normalize_newlines,
    read_text,
    resolve_repo_path,
    resolve_task_dir,
    run_cmd,
)


def apply_safe_fixes(task_dir: Path, *, fix_git: bool = True) -> list[str]:
    actions: list[str] = []

    # instruction sync
    instr = task_dir / "instruction.md"
    prob = task_dir / "environment" / "problem_statement.md"
    if instr.is_file():
        text = normalize_newlines(read_text(instr))
        prob.parent.mkdir(parents=True, exist_ok=True)
        if not prob.is_file() or normalize_newlines(read_text(prob)) != text:
            prob.write_text(text, encoding="utf-8", newline="\n")
            actions.append("synced environment/problem_statement.md from instruction.md")

    # golden.patch rename
    solution = task_dir / "solution"
    golden = solution / "golden.patch"
    if solution.is_dir() and not golden.is_file():
        for legacy in LEGACY_PATCH_NAMES:
            src = solution / legacy
            if src.is_file():
                src.rename(golden)
                actions.append(f"renamed solution/{legacy} -> golden.patch")
                break

    # CRLF -> LF on shell scripts
    for rel in ("tests/test.sh", "solution/solve.sh"):
        path = task_dir / rel
        if not path.is_file():
            continue
        raw = path.read_bytes()
        if b"\r" in raw:
            path.write_bytes(raw.replace(b"\r\n", b"\n").replace(b"\r", b"\n"))
            actions.append(f"normalized LF line endings: {rel}")

    if fix_git:
        repo = task_dir / "environment" / "repo"
        git_logs = repo / ".git" / "logs"
        if git_logs.exists():
            shutil.rmtree(git_logs, ignore_errors=True)
            actions.append("removed environment/repo/.git/logs")

        # drop remotes
        if (repo / ".git").exists():
            code, remotes, _ = check_git_hygiene.git(repo, "remote")
            if code == 0 and remotes.strip():
                for remote in remotes.splitlines():
                    remote = remote.strip()
                    if remote:
                        run_cmd(["git", "remote", "remove", remote], cwd=repo, timeout=30)
                        actions.append(f"removed git remote {remote}")

        # realign base_commit_sha
        before = check_git_hygiene.run_hygiene(task_dir, fix=True)
        for f in before.findings:
            if f.check == "base_commit_match" and "realigned" in f.message:
                actions.append("realigned base_commit_sha to HEAD")
                break

    return actions


def prepare_one(
    source: Path,
    *,
    force: bool = False,
    fix_git: bool = True,
    skip_package: bool = False,
) -> dict[str, Any]:
    source = resolve_repo_path(source)
    if source.suffix.lower() == ".zip" and source.is_file():
        task_dir = ingest_zip.ingest(source, force=force)
        ingested = True
    else:
        task_dir = resolve_task_dir(source)
        ingested = False
        if not task_dir.is_dir():
            raise FileNotFoundError(f"task dir not found: {task_dir}")

    actions = apply_safe_fixes(task_dir, fix_git=fix_git)
    static_report = run_static_checks.run_checks(task_dir)
    git_report = check_git_hygiene.run_hygiene(task_dir, fix=False)

    result: dict[str, Any] = {
        "source": str(source),
        "task_dir": str(task_dir),
        "ingested": ingested,
        "auto_fixes": actions,
        "static": static_report.to_json(),
        "git": git_report.to_json(),
    }

    blocking = static_report.status == "FAIL" or git_report.status == "FAIL"
    if skip_package:
        result["status"] = "CHECKS_ONLY"
        result["blocking"] = blocking
        return result

    if blocking:
        result["status"] = "FAIL"
        result["zip"] = None
        result["message"] = "checks failed; zip not written (fix remaining FAILs then re-run)"
        return result

    packaged = package_task.package_task(task_dir, out_dir=READY_DIR, validate=True)
    result["package"] = packaged
    result["zip"] = packaged.get("zip")
    result["status"] = packaged.get("status", "FAIL")
    return result


def main() -> int:
    ensure_python()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "source",
        nargs="?",
        help="Zip path, task dir, or omit to process all incoming/*.zip",
    )
    parser.add_argument("--force", action="store_true", help="Replace existing tasks/<name>")
    parser.add_argument("--no-fix-git", action="store_true")
    parser.add_argument("--skip-package", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    if args.source:
        sources = [resolve_repo_path(args.source)]
    else:
        sources = sorted(INCOMING_DIR.glob("*.zip"))
        if not sources:
            # also allow preparing every tasks/* that has task.toml
            sources = sorted(p for p in TASKS_DIR.iterdir() if (p / "task.toml").is_file())
        if not sources:
            print("nothing to prepare (drop a zip in incoming/ or pass a path)", file=sys.stderr)
            return 2

    overall = 0
    results = []
    for source in sources:
        try:
            result = prepare_one(
                source,
                force=args.force,
                fix_git=not args.no_fix_git,
                skip_package=args.skip_package,
            )
        except Exception as exc:  # noqa: BLE001
            result = {"source": str(source), "status": "FAIL", "error": str(exc)}
            overall = 1
        results.append(result)
        if result.get("status") == "FAIL":
            overall = 1

    if args.json:
        print(json.dumps(results if len(results) > 1 else results[0], indent=2))
    else:
        for result in results:
            print(f"source: {result.get('source')}")
            print(f"task:   {result.get('task_dir')}")
            print(f"status: {result.get('status')}")
            for action in result.get("auto_fixes") or []:
                print(f"  fix: {action}")
            if result.get("error"):
                print(f"  error: {result['error']}")
            for section in ("static", "git"):
                block = result.get(section) or {}
                for f in block.get("findings") or []:
                    if f.get("status") != "PASS":
                        print(f"  [{f['status']}] {section}/{f['check']}: {f['message']}")
            if result.get("zip"):
                print(f"zip:    {result['zip']}")
            if result.get("message"):
                print(f"note:   {result['message']}")
            print()
    return overall


if __name__ == "__main__":
    raise SystemExit(main())
