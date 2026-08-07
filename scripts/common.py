"""Shared paths and helpers for Sentinel Ultra tooling."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
INCOMING_DIR = REPO_ROOT / "incoming"
TASKS_DIR = REPO_ROOT / "tasks"
READY_DIR = REPO_ROOT / "Task_Ready_To_Submit"

REQUIRED_ROOT_FILES = ("instruction.md", "task.toml")
REQUIRED_ROOT_DIRS = ("environment", "solution", "tests")
REQUIRED_ENV_FILES = (
    "environment/Dockerfile",
    "environment/problem_statement.md",
)
REQUIRED_SOLUTION_FILES = ("solution/solve.sh", "solution/golden.patch")
REQUIRED_TEST_FILES = ("tests/test.sh", "tests/tests.patch", "tests/config.json")

# May exist during authoring; never ship in submission zip.
FORBIDDEN_ROOT_NAMES = frozenset(
    {
        "runs",
        "jobs",
        "metadata.json",
        "rubric.txt",
        "rubrics.txt",
        "AGENTS.md",
        "CLAUDE.md",
        "skills.md",
        "review_answer.txt",
    }
)

BANNED_AI_FILENAMES_CI = frozenset(
    {
        "claude.md",
        "claude.txt",
        "agents.md",
        "agent.md",
        "skills.md",
        "skill.md",
        "ai_instructions.md",
        "ai_notes.md",
        "agent_notes.md",
        "copilot.md",
        "copilot-instructions.md",
        ".cursorrules",
    }
)
BANNED_AI_DIRECTORY_NAMES_CI = frozenset(
    {".cursor", ".aider", ".continue", ".claude"}
)

LEGACY_PATCH_NAMES = ("solution.patch", "init_state.patch")

STATUS_ORDER = {"PASS": 0, "WARN": 1, "FAIL": 2}


@dataclass
class Finding:
    check: str
    status: str  # PASS | WARN | FAIL
    message: str
    fixable: bool = False
    details: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        payload = {
            "check": self.check,
            "status": self.status,
            "message": self.message,
            "fixable": self.fixable,
        }
        if self.details:
            payload["details"] = self.details
        return payload


@dataclass
class Report:
    task_dir: str
    findings: list[Finding] = field(default_factory=list)

    @property
    def status(self) -> str:
        worst = "PASS"
        for f in self.findings:
            if STATUS_ORDER[f.status] > STATUS_ORDER[worst]:
                worst = f.status
        return worst

    def add(self, finding: Finding) -> None:
        self.findings.append(finding)

    def to_json(self) -> dict[str, Any]:
        return {
            "task_dir": self.task_dir,
            "status": self.status,
            "fails": sum(1 for f in self.findings if f.status == "FAIL"),
            "warns": sum(1 for f in self.findings if f.status == "WARN"),
            "findings": [f.to_json() for f in self.findings],
        }


def resolve_repo_path(path: Path | str) -> Path:
    p = Path(path)
    return p if p.is_absolute() else REPO_ROOT / p


def resolve_task_dir(path: Path | str) -> Path:
    p = resolve_repo_path(path)
    if p.is_dir() and (p / "task.toml").exists():
        return p
    # Allow bare task name under tasks/
    candidate = TASKS_DIR / Path(path).name
    if candidate.is_dir() and (candidate / "task.toml").exists():
        return candidate
    return p


def run_cmd(
    argv: list[str],
    *,
    cwd: Path | None = None,
    timeout: int = 60,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=timeout,
        encoding="utf-8",
        errors="replace",
    )


def load_toml(path: Path) -> dict[str, Any]:
    try:
        import tomllib
    except ModuleNotFoundError as exc:  # pragma: no cover
        raise SystemExit("Python 3.11+ required (tomllib)") from exc
    with path.open("rb") as fh:
        return tomllib.load(fh)


def load_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def normalize_newlines(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def find_harbor_root(extract_dir: Path) -> Path:
    """Locate Harbor task root inside an extracted archive."""
    if (extract_dir / "task.toml").exists():
        return extract_dir
    # Nested single folder
    children = [p for p in extract_dir.iterdir() if p.name not in {".DS_Store", "__MACOSX"}]
    dirs = [p for p in children if p.is_dir()]
    if len(dirs) == 1 and (dirs[0] / "task.toml").exists():
        return dirs[0]
    # task/ or seed/ wrappers
    for name in ("task", "seed"):
        nested = extract_dir / name
        if (nested / "task.toml").exists():
            return nested
    # Search one level deep
    for child in dirs:
        if (child / "task.toml").exists():
            return child
        for name in ("task", "seed"):
            nested = child / name
            if (nested / "task.toml").exists():
                return nested
    raise FileNotFoundError(
        f"Could not find task.toml under {extract_dir}. Expected flat Harbor layout."
    )


def slugify_task_name(name: str) -> str:
    base = Path(name).stem
    base = re.sub(r"[^\w.\-]+", "-", base).strip("-._")
    return base or "task"


def print_report(report: Report, *, as_json: bool = False) -> int:
    if as_json:
        print(json.dumps(report.to_json(), indent=2))
    else:
        print(f"task: {report.task_dir}")
        print(f"status: {report.status}")
        for f in report.findings:
            flag = "fixable" if f.fixable else "manual"
            print(f"  [{f.status}] {f.check}: {f.message} ({flag})")
    return 0 if report.status != "FAIL" else 1


def ensure_python() -> None:
    if sys.version_info < (3, 11):
        raise SystemExit(f"Python 3.11+ required; found {sys.version.split()[0]}")
