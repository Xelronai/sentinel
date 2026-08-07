#!/usr/bin/env python3
"""
Static Sentinel Ultra checks for one Harbor task directory.

Does not edit environment/repo tracked source. Does not run Harbor.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.common import (  # noqa: E402
    FORBIDDEN_ROOT_NAMES,
    LEGACY_PATCH_NAMES,
    REQUIRED_ENV_FILES,
    REQUIRED_ROOT_DIRS,
    REQUIRED_ROOT_FILES,
    REQUIRED_SOLUTION_FILES,
    REQUIRED_TEST_FILES,
    Finding,
    Report,
    ensure_python,
    load_json,
    load_toml,
    normalize_newlines,
    print_report,
    read_text,
    resolve_task_dir,
)

CPUS_ALLOWED = {2, 4}
MEMORY_RANGE = (2048, 16384)
STORAGE_RANGE = (5120, 10240)
BUILD_TIMEOUT_MAX = 1800
AGENT_TIMEOUT_MAX = 7200
VERIFIER_TIMEOUT_MAX = 1800
MIN_FAIL_TO_PASS = 10

# Soft leakage hints (warn only — judgment still human).
LEAKAGE_PATTERNS = [
    re.compile(r"https?://github\.com/[^\s]+/pull/\d+", re.I),
    re.compile(r"\bPR\s*#?\d+\b"),
    re.compile(r"look in (the )?(file|module|function)\b", re.I),
    re.compile(r"\byou should edit\b", re.I),
    re.compile(r"\bimplement by\b", re.I),
]

# Local stand-in for platform: scripts.harbor.checks.cdg_sentinel_ultra.prescriptiveness
# (CodeBuild often fails BUILD with exit 1 on these).
PRESCRIPTIVENESS_RULES: list[tuple[str, str, re.Pattern[str], str]] = [
    (
        "P_how_argv",
        "FAIL",
        re.compile(
            r"(?:via|using|through)\s+(?:the\s+)?(?:platform\s+)?command\s+interpreter"
            r"|passed through in argv form"
            r"|argv form via"
            r"|do not fold(?:ed)? into a POSIX-quoted"
            r"|rather than folded into",
            re.I,
        ),
        "Prescribes delivery/mechanism (HOW) instead of observable contract (WHAT)",
    ),
    (
        "P_exact_api",
        "FAIL",
        re.compile(
            r"process\.platform\s*===\s*['\"]win32['\"]"
            r"|os\.platform\(\)"
            r"|navigator\.platform"
            r"|sys\.platform\s*==",
            re.I,
        ),
        "Names exact runtime API as required detection — describe the environment property instead",
    ),
    (
        "P_env_checklist",
        "FAIL",
        re.compile(
            r"(?:signalled|signaled|detected|indicated)\s+by\s+`?[A-Z][A-Z0-9_]+`?"
            r"(?:\s+or\s+`?[A-Z][A-Z0-9_]+`?)+"
            r"|`(?:MSYSTEM|MINGW_PREFIX|OSTYPE)`",
            re.I,
        ),
        "Pre-answers exact env-var discovery; describe the compatibility layer, not signal names",
    ),
    (
        "P_must_call",
        "FAIL",
        re.compile(
            r"\b(?:must|should)\s+(?:call|invoke|use|import)\s+`?[A-Za-z_][\w.]*`?"
            r"|\bimplement\s+(?:this|it)\s+by\b"
            r"|\buse\s+the\s+following\s+(?:approach|algorithm|code)\b",
            re.I,
        ),
        "Prescribes a specific call/import/approach rather than the required outcome",
    ),
    (
        "P_edit_here",
        "FAIL",
        re.compile(
            r"\bedit\s+(?:the\s+)?(?:file|function|method|class)\b"
            r"|\bchange\s+lines?\s+\d+"
            r"|\bin\s+[`']?[\w./-]+\.(?:py|ts|js|go|rs|java)[`']?\s*,?\s*(?:add|set|fix|replace)\b",
            re.I,
        ),
        "Points at exact edit sites — leakage / over-prescription",
    ),
]


def check_layout(task_dir: Path, report: Report) -> None:
    missing: list[str] = []
    for name in REQUIRED_ROOT_FILES:
        if not (task_dir / name).is_file():
            missing.append(name)
    for name in REQUIRED_ROOT_DIRS:
        if not (task_dir / name).is_dir():
            missing.append(name + "/")
    for rel in REQUIRED_ENV_FILES + REQUIRED_TEST_FILES:
        if not (task_dir / rel).is_file():
            missing.append(rel)

    solution_dir = task_dir / "solution"
    has_golden = (solution_dir / "golden.patch").is_file()
    legacy = [n for n in LEGACY_PATCH_NAMES if (solution_dir / n).is_file()]
    if not (solution_dir / "solve.sh").is_file():
        missing.append("solution/solve.sh")
    if not has_golden and not legacy:
        missing.append("solution/golden.patch")

    if missing:
        report.add(
            Finding(
                "layout",
                "FAIL",
                f"missing required Harbor paths: {', '.join(missing)}",
                fixable=bool(legacy) and "solution/golden.patch" in missing,
                details={"missing": missing, "legacy_patches": legacy},
            )
        )
    else:
        report.add(Finding("layout", "PASS", "required Harbor layout present"))

    if legacy and has_golden:
        report.add(
            Finding(
                "legacy_patch",
                "WARN",
                f"legacy patch still present alongside golden.patch: {', '.join(legacy)}",
                fixable=True,
                details={"legacy_patches": legacy},
            )
        )
    elif legacy and not has_golden:
        report.add(
            Finding(
                "legacy_patch",
                "FAIL",
                f"rename {legacy[0]} to golden.patch (old reverse-patch model is obsolete)",
                fixable=True,
                details={"legacy_patches": legacy},
            )
        )


def check_instruction_sync(task_dir: Path, report: Report) -> None:
    instr = task_dir / "instruction.md"
    prob = task_dir / "environment" / "problem_statement.md"
    if not instr.is_file() or not prob.is_file():
        return
    a = normalize_newlines(read_text(instr))
    b = normalize_newlines(read_text(prob))
    if a == b:
        report.add(Finding("instruction_sync", "PASS", "problem_statement.md matches instruction.md"))
    else:
        report.add(
            Finding(
                "instruction_sync",
                "FAIL",
                "environment/problem_statement.md must be an exact copy of instruction.md",
                fixable=True,
            )
        )


def check_instruction_leakage(task_dir: Path, report: Report) -> None:
    instr = task_dir / "instruction.md"
    if not instr.is_file():
        return
    text = read_text(instr)
    hits = []
    for pat in LEAKAGE_PATTERNS:
        m = pat.search(text)
        if m:
            hits.append(m.group(0))
    if hits:
        report.add(
            Finding(
                "instruction_leakage",
                "WARN",
                "possible instruction leakage / over-prescription cues",
                fixable=False,
                details={"samples": hits[:5]},
            )
        )
    else:
        report.add(Finding("instruction_leakage", "PASS", "no simple leakage regex hits"))


def check_instruction_prescriptiveness(task_dir: Path, report: Report) -> None:
    """Catch CDG-style over-prescription before zip (blocks prepare_task on FAIL)."""
    instr = task_dir / "instruction.md"
    if not instr.is_file():
        return
    text = read_text(instr)
    hits: list[dict[str, str]] = []
    worst = "PASS"
    for rule_id, severity, pat, message in PRESCRIPTIVENESS_RULES:
        m = pat.search(text)
        if not m:
            continue
        hits.append(
            {
                "id": rule_id,
                "severity": severity,
                "evidence": m.group(0)[:160],
                "message": message,
            }
        )
        if severity == "FAIL":
            worst = "FAIL"
        elif severity == "WARN" and worst == "PASS":
            worst = "WARN"

    if not hits:
        report.add(
            Finding(
                "prescriptiveness",
                "PASS",
                "no CDG-style prescriptiveness regex hits (still re-read instruction manually)",
            )
        )
        return

    report.add(
        Finding(
            "prescriptiveness",
            worst,
            "instruction over-prescriptive for platform CDG — rewrite WHAT not HOW, "
            "sync problem_statement.md, then re-zip",
            fixable=False,
            details={
                "findings": hits,
                "samples": [f"{h['id']}: {h['evidence']}" for h in hits[:5]],
            },
        )
    )


def _get_nested(data: dict[str, Any], *keys: str) -> Any:
    cur: Any = data
    for key in keys:
        if not isinstance(cur, dict) or key not in cur:
            return None
        cur = cur[key]
    return cur


def check_task_toml(task_dir: Path, report: Report) -> None:
    path = task_dir / "task.toml"
    if not path.is_file():
        return
    try:
        data = load_toml(path)
    except Exception as exc:  # noqa: BLE001
        report.add(Finding("task_toml_parse", "FAIL", f"task.toml parse error: {exc}"))
        return

    meta_keys = ("source", "category", "difficulty_explanation", "schema_version")
    # metadata may be top-level or under [metadata]
    missing_meta = []
    for key in meta_keys:
        if key not in data and _get_nested(data, "metadata", key) is None:
            missing_meta.append(key)
    if missing_meta:
        report.add(
            Finding(
                "task_toml_metadata",
                "WARN",
                f"missing recommended metadata keys: {', '.join(missing_meta)}",
                details={"missing": missing_meta},
            )
        )
    else:
        report.add(Finding("task_toml_metadata", "PASS", "source/category/difficulty_explanation/schema_version present"))

    env = data.get("environment") if isinstance(data.get("environment"), dict) else {}
    agent = data.get("agent") if isinstance(data.get("agent"), dict) else {}
    verifier = data.get("verifier") if isinstance(data.get("verifier"), dict) else {}

    issues: list[str] = []

    cpus = env.get("cpus", data.get("cpus"))
    if cpus is not None and cpus not in CPUS_ALLOWED:
        issues.append(f"cpus must be 2 or 4 (got {cpus})")

    memory = env.get("memory_mb", data.get("memory_mb"))
    if memory is not None and not (MEMORY_RANGE[0] <= int(memory) <= MEMORY_RANGE[1]):
        issues.append(f"memory_mb must be {MEMORY_RANGE[0]}-{MEMORY_RANGE[1]} (got {memory})")

    storage = env.get("storage_mb", data.get("storage_mb"))
    if storage is not None and not (STORAGE_RANGE[0] <= int(storage) <= STORAGE_RANGE[1]):
        issues.append(f"storage_mb must be {STORAGE_RANGE[0]}-{STORAGE_RANGE[1]} (got {storage})")

    gpus = env.get("gpus", data.get("gpus"))
    if gpus is not None and int(gpus) != 0:
        issues.append(f"gpus must be 0 (got {gpus})")

    build_to = env.get("build_timeout_sec", data.get("build_timeout_sec"))
    if build_to is not None and int(build_to) > BUILD_TIMEOUT_MAX:
        issues.append(f"build_timeout_sec max {BUILD_TIMEOUT_MAX} (got {build_to})")

    agent_to = agent.get("timeout_sec")
    if agent_to is not None and int(agent_to) > AGENT_TIMEOUT_MAX:
        issues.append(f"agent.timeout_sec max {AGENT_TIMEOUT_MAX} (got {agent_to})")

    ver_to = verifier.get("timeout_sec")
    if ver_to is not None and int(ver_to) > VERIFIER_TIMEOUT_MAX:
        issues.append(f"verifier.timeout_sec max {VERIFIER_TIMEOUT_MAX} (got {ver_to})")

    # network modes
    env_net = env.get("network_mode")
    agent_net = agent.get("network_mode")
    ver_net = verifier.get("network_mode")
    if env_net != "public":
        issues.append(f'environment.network_mode must be "public" (got {env_net!r})')
    if agent_net != "allowlist":
        issues.append(f'agent.network_mode must be "allowlist" (got {agent_net!r})')
    else:
        hosts = agent.get("allowed_hosts")
        if hosts != ["api.portkey.ai"] and hosts != ("api.portkey.ai",):
            issues.append('agent.allowed_hosts must be ["api.portkey.ai"]')
    if ver_net != "no-network":
        issues.append(f'verifier.network_mode must be "no-network" (got {ver_net!r})')

    if issues:
        report.add(
            Finding(
                "task_toml_limits",
                "FAIL",
                "task.toml resource/network constraints violated",
                fixable=True,
                details={"issues": issues},
            )
        )
    else:
        report.add(Finding("task_toml_limits", "PASS", "task.toml limits and network_mode ok"))

    # base_commit_sha presence
    base = (
        data.get("base_commit_sha")
        or _get_nested(data, "metadata", "base_commit_sha")
        or env.get("base_commit_sha")
    )
    # also common under [harbor] or top-level in various schemas
    if base is None:
        for section in ("harbor", "repo", "git"):
            sec = data.get(section)
            if isinstance(sec, dict) and sec.get("base_commit_sha"):
                base = sec["base_commit_sha"]
                break
    if not base:
        # scan raw text for key
        raw = read_text(path)
        if "base_commit_sha" not in raw:
            report.add(
                Finding(
                    "base_commit_sha",
                    "WARN",
                    "base_commit_sha not found in task.toml (git hygiene cannot align)",
                )
            )
        else:
            report.add(Finding("base_commit_sha", "PASS", "base_commit_sha key present in task.toml"))
    else:
        report.add(
            Finding(
                "base_commit_sha",
                "PASS",
                f"base_commit_sha present ({str(base)[:12]}…)",
                details={"base_commit_sha": str(base)},
            )
        )


def check_config_json(task_dir: Path, report: Report) -> None:
    path = task_dir / "tests" / "config.json"
    if not path.is_file():
        return
    try:
        data = load_json(path)
    except Exception as exc:  # noqa: BLE001
        report.add(Finding("config_json", "FAIL", f"config.json parse error: {exc}"))
        return

    grading = data.get("grading") if isinstance(data.get("grading"), dict) else data
    f2p = grading.get("fail_to_pass") if isinstance(grading, dict) else None
    p2p = grading.get("pass_to_pass") if isinstance(grading, dict) else None

    if not isinstance(f2p, list) or not f2p:
        report.add(
            Finding(
                "fail_to_pass",
                "FAIL",
                "grading.fail_to_pass must be a non-empty list",
                fixable=False,
            )
        )
    elif len(f2p) < MIN_FAIL_TO_PASS:
        report.add(
            Finding(
                "fail_to_pass",
                "FAIL",
                f"fail_to_pass has {len(f2p)} ids; need ≥{MIN_FAIL_TO_PASS}",
                fixable=False,
                details={"count": len(f2p)},
            )
        )
    else:
        report.add(
            Finding(
                "fail_to_pass",
                "PASS",
                f"fail_to_pass count {len(f2p)} (≥{MIN_FAIL_TO_PASS})",
                details={"count": len(f2p)},
            )
        )

    if p2p is None:
        report.add(Finding("pass_to_pass", "WARN", "pass_to_pass missing (regression guard recommended)"))
    elif not isinstance(p2p, list):
        report.add(Finding("pass_to_pass", "FAIL", "pass_to_pass must be a list"))
    else:
        report.add(Finding("pass_to_pass", "PASS", f"pass_to_pass present ({len(p2p)} ids)"))


def check_dockerfile(task_dir: Path, report: Report) -> None:
    path = task_dir / "environment" / "Dockerfile"
    if not path.is_file():
        return
    text = read_text(path)
    issues: list[str] = []
    warns: list[str] = []

    if re.search(r"^FROM\s+\S+:latest\b", text, re.M | re.I):
        issues.append("FROM ...:latest must be pinned to a concrete tag")
    if "COPY" in text and "frozen-requirements.txt" in text:
        frozen = task_dir / "environment" / "frozen-requirements.txt"
        if not frozen.is_file():
            # also check repo root inside environment
            alt = task_dir / "environment" / "repo" / "frozen-requirements.txt"
            if not alt.is_file():
                issues.append("Dockerfile COPYs frozen-requirements.txt but file is missing")
    if re.search(r"\balpine\b", text, re.I) and not re.search(r"\bbash\b", text):
        warns.append("Alpine image may need `apk add --no-cache bash`")

    compose = list(task_dir.glob("**/docker_compose.y*ml")) + list(
        (task_dir / "environment").glob("**/docker-compose.y*ml")
    )
    for cpath in compose:
        ctext = read_text(cpath)
        if re.search(r'network_mode\s*=\s*"none"|network_mode:\s*none', ctext):
            issues.append(f'{cpath.relative_to(task_dir)} has network_mode none (remove it)')

    if issues:
        report.add(
            Finding(
                "dockerfile",
                "FAIL",
                "Dockerfile / compose issues",
                fixable=True,
                details={"issues": issues, "warns": warns},
            )
        )
    elif warns:
        report.add(Finding("dockerfile", "WARN", "; ".join(warns), fixable=True, details={"warns": warns}))
    else:
        report.add(Finding("dockerfile", "PASS", "Dockerfile sanity ok"))


def check_stray_artifacts(task_dir: Path, report: Report) -> None:
    bad: list[str] = []
    for name in FORBIDDEN_ROOT_NAMES:
        p = task_dir / name
        if p.exists():
            bad.append(name)
    # solution legacy names already covered
    if (task_dir / "metadata.json").is_file():
        if "metadata.json" not in bad:
            bad.append("metadata.json")
    if bad:
        report.add(
            Finding(
                "stray_artifacts",
                "WARN",
                f"authoring/extra paths present (excluded from zip): {', '.join(bad)}",
                fixable=True,
                details={"paths": bad},
            )
        )
    else:
        report.add(Finding("stray_artifacts", "PASS", "no forbidden root authoring artifacts"))


def check_shebang_crlf(task_dir: Path, report: Report) -> None:
    scripts = [
        task_dir / "tests" / "test.sh",
        task_dir / "solution" / "solve.sh",
    ]
    problems: list[str] = []
    for path in scripts:
        if not path.is_file():
            continue
        raw = path.read_bytes()
        if b"\r\n" in raw or raw.endswith(b"\r"):
            problems.append(f"{path.relative_to(task_dir)} has CRLF")
        text = raw.decode("utf-8", errors="replace")
        if text.startswith("#!") and not text.startswith("#!/"):
            problems.append(f"{path.relative_to(task_dir)} has odd shebang")
    if problems:
        report.add(
            Finding(
                "scripts_line_endings",
                "FAIL",
                "shell scripts need LF + valid shebang",
                fixable=True,
                details={"issues": problems},
            )
        )
    else:
        report.add(Finding("scripts_line_endings", "PASS", "solve.sh / test.sh line endings ok"))


def run_checks(task_dir: Path) -> Report:
    report = Report(task_dir=str(task_dir))
    check_layout(task_dir, report)
    check_instruction_sync(task_dir, report)
    check_instruction_leakage(task_dir, report)
    check_instruction_prescriptiveness(task_dir, report)
    check_task_toml(task_dir, report)
    check_config_json(task_dir, report)
    check_dockerfile(task_dir, report)
    check_stray_artifacts(task_dir, report)
    check_shebang_crlf(task_dir, report)
    return report


def main() -> int:
    ensure_python()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("task_dir", help="Path or name under tasks/")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    task_dir = resolve_task_dir(args.task_dir)
    if not task_dir.is_dir():
        print(f"task dir not found: {task_dir}", file=sys.stderr)
        return 2
    report = run_checks(task_dir)
    return print_report(report, as_json=args.json)


if __name__ == "__main__":
    raise SystemExit(main())
