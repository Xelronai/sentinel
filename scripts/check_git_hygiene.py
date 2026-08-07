#!/usr/bin/env python3
"""
Git hygiene checks for environment/repo (Sentinel Ultra).

Never modifies tracked source files — only reads git state / optionally
realigns task.toml base_commit_sha when --fix is passed.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.dont_write_bytecode = True
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.common import (  # noqa: E402
    Finding,
    Report,
    ensure_python,
    load_toml,
    print_report,
    read_text,
    resolve_task_dir,
)

GIT_SIZE_LIMIT_BYTES = 100 * 1024 * 1024


def git(repo: Path, *args: str, timeout: int = 60) -> tuple[int, str, str]:
    from scripts.common import run_cmd

    result = run_cmd(["git", *args], cwd=repo, timeout=timeout)
    return result.returncode, result.stdout.strip(), result.stderr.strip()


def find_base_commit_sha(task_dir: Path) -> tuple[str | None, Path | None]:
    """Return (sha, toml_path) if base_commit_sha is found."""
    toml_path = task_dir / "task.toml"
    if not toml_path.is_file():
        return None, None
    raw = read_text(toml_path)
    m = re.search(r'(?m)^\s*base_commit_sha\s*=\s*["\']?([0-9a-fA-F]{7,40})["\']?', raw)
    if m:
        return m.group(1), toml_path
    try:
        data = load_toml(toml_path)
    except Exception:  # noqa: BLE001
        return None, toml_path
    # shallow walk
    stack = [data]
    while stack:
        cur = stack.pop()
        if isinstance(cur, dict):
            if "base_commit_sha" in cur and isinstance(cur["base_commit_sha"], str):
                return cur["base_commit_sha"], toml_path
            stack.extend(cur.values())
    return None, toml_path


def realign_base_commit_sha(toml_path: Path, new_sha: str) -> bool:
    raw = read_text(toml_path)
    new_raw, n = re.subn(
        r'(?m)^(\s*base_commit_sha\s*=\s*)(["\']?)([0-9a-fA-F]{7,40})(["\']?)',
        rf'\1\g<2>{new_sha}\4',
        raw,
        count=1,
    )
    if n == 0:
        return False
    toml_path.write_text(new_raw, encoding="utf-8", newline="\n")
    return True


def dir_size(path: Path) -> int:
    total = 0
    for p in path.rglob("*"):
        if p.is_file():
            try:
                total += p.stat().st_size
            except OSError:
                pass
    return total


def run_hygiene(task_dir: Path, *, fix: bool = False) -> Report:
    report = Report(task_dir=str(task_dir))
    repo = task_dir / "environment" / "repo"
    if not repo.is_dir():
        report.add(Finding("repo_present", "FAIL", "environment/repo missing"))
        return report
    git_dir = repo / ".git"
    if not git_dir.exists():
        report.add(Finding("git_dir", "FAIL", "environment/repo/.git missing"))
        return report
    report.add(Finding("repo_present", "PASS", "environment/repo with .git present"))

    code, head, err = git(repo, "rev-parse", "--verify", "HEAD")
    if code != 0:
        report.add(Finding("head_resolves", "FAIL", f"HEAD does not resolve: {err or head}"))
        return report
    report.add(Finding("head_resolves", "PASS", f"HEAD={head}"))

    base, toml_path = find_base_commit_sha(task_dir)
    if not base:
        report.add(Finding("base_commit_match", "WARN", "base_commit_sha not found in task.toml"))
    else:
        # compare full or prefix
        if head.startswith(base) or base.startswith(head) or head == base:
            report.add(Finding("base_commit_match", "PASS", "HEAD matches base_commit_sha"))
        else:
            msg = f"HEAD ({head[:12]}) != base_commit_sha ({base[:12]})"
            if fix and toml_path:
                if realign_base_commit_sha(toml_path, head):
                    report.add(
                        Finding(
                            "base_commit_match",
                            "PASS",
                            f"{msg}; realigned task.toml to HEAD",
                            fixable=True,
                        )
                    )
                else:
                    report.add(Finding("base_commit_match", "FAIL", f"{msg}; failed to rewrite toml", fixable=True))
            else:
                report.add(Finding("base_commit_match", "FAIL", msg, fixable=True))

    code, leaked, err = git(repo, "rev-list", "--all", "--not", "HEAD")
    if code != 0:
        report.add(Finding("no_leaked_history", "FAIL", f"rev-list failed: {err}"))
    elif leaked:
        commits = [c for c in leaked.splitlines() if c.strip()]
        report.add(
            Finding(
                "no_leaked_history",
                "FAIL",
                f"{len(commits)} commit(s) reachable beyond HEAD (leaked fix history)",
                fixable=True,
                details={"sample": commits[:5]},
            )
        )
    else:
        report.add(Finding("no_leaked_history", "PASS", "no commits beyond HEAD"))

    code, remotes, err = git(repo, "remote")
    if code != 0:
        report.add(Finding("no_remotes", "FAIL", f"git remote failed: {err}"))
    elif remotes.strip():
        report.add(
            Finding(
                "no_remotes",
                "FAIL",
                f"git remotes must be empty (got: {remotes.replace(chr(10), ', ')})",
                fixable=True,
            )
        )
    else:
        report.add(Finding("no_remotes", "PASS", "no git remotes"))

    code, filters, err = git(repo, "config", "--local", "--get-regexp", r"^filter\.")
    # exit 1 means no matches — good
    if code == 0 and filters.strip():
        report.add(
            Finding(
                "no_filters",
                "FAIL",
                "local git filter.* config must be empty",
                fixable=True,
                details={"filters": filters.splitlines()[:10]},
            )
        )
    else:
        report.add(Finding("no_filters", "PASS", "no local filter.* config"))

    code, status, err = git(repo, "status", "--porcelain")
    if code != 0:
        report.add(Finding("clean_tree", "FAIL", f"git status failed: {err}"))
    elif status.strip():
        report.add(
            Finding(
                "clean_tree",
                "FAIL",
                "git status not clean",
                fixable=True,
                details={"status": status.splitlines()[:20]},
            )
        )
    else:
        report.add(Finding("clean_tree", "PASS", "working tree clean"))

    logs = git_dir / "logs"
    if logs.exists():
        report.add(
            Finding(
                "no_git_logs",
                "FAIL",
                ".git/logs must not be present before upload",
                fixable=True,
            )
        )
    else:
        report.add(Finding("no_git_logs", "PASS", "no .git/logs"))

    size = dir_size(git_dir)
    if size >= GIT_SIZE_LIMIT_BYTES:
        report.add(
            Finding(
                "git_size",
                "FAIL",
                f".git is {size / (1024 * 1024):.1f} MB (limit 100 MB)",
                fixable=True,
                details={"bytes": size},
            )
        )
    else:
        report.add(
            Finding(
                "git_size",
                "PASS",
                f".git size {size / (1024 * 1024):.1f} MB",
                details={"bytes": size},
            )
        )

    # tests.patch apply --check (from environment/repo)
    patch = task_dir / "tests" / "tests.patch"
    if patch.is_file():
        code, _out, err = git(repo, "apply", "--check", str(patch.resolve()), timeout=120)
        if code != 0:
            report.add(
                Finding(
                    "tests_patch_apply",
                    "FAIL",
                    "tests.patch does not apply cleanly at HEAD",
                    fixable=False,
                    details={"stderr": err[:500]},
                )
            )
        else:
            report.add(Finding("tests_patch_apply", "PASS", "tests.patch applies cleanly (--check)"))
    else:
        report.add(Finding("tests_patch_apply", "FAIL", "tests/tests.patch missing"))

    return report


def main() -> int:
    ensure_python()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("task_dir")
    parser.add_argument("--fix", action="store_true", help="Realign base_commit_sha to HEAD")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    task_dir = resolve_task_dir(args.task_dir)
    if not task_dir.is_dir():
        print(f"task dir not found: {task_dir}", file=sys.stderr)
        return 2
    report = run_hygiene(task_dir, fix=args.fix)
    return print_report(report, as_json=args.json)


if __name__ == "__main__":
    raise SystemExit(main())
