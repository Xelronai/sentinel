# AGENTS.md

Sentinel Ultra local tooling. Unit: `tasks/<task-name>/`.
Policy: `.cursor/rules/sentinel-ultra-*.mdc`. Commands: `commands.md`.

## Must

1. Never edit tracked source under `environment/repo/`.
2. Keep `environment/problem_statement.md` identical to `instruction.md`.
3. Gold patch file is `solution/golden.patch` (not `solution.patch` / `init_state.patch`).
4. Do not claim READY/SUBMIT without command output from `prepare_task` / checks.
5. Upload zip must be **flat** Harbor root; never include `runs/`.
6. `grading.fail_to_pass` must have ≥10 ids.
7. Verdict labels: Valid as-is / Fixable / Not Fixable (never old Valid/Invalid alone).
8. Host Python is not container Python.
9. Instruction must pass **prescriptiveness** (platform CDG): state WHAT/outcomes, not HOW
   (no exact API recipes, argv delivery prescriptions, or env-var discovery checklists).
   See `.cursor/rules/sentinel-ultra-zip-ready.mdc`.

## Pipeline

```text
incoming/*.zip
  -> ingest_zip
  -> safe auto-fixes
  -> run_static_checks + check_git_hygiene
  -> package_task
  -> Task_Ready_To_Submit/<name>.zip
```

Start long sessions with:

```bash
python scripts/sentinel_doctor.py
```

## Zip bans

`runs/`, `metadata.json`, AI scaffolding (`.cursor/`, `AGENTS.md`, `CLAUDE.md`,
`skills.md`, …), `__pycache__`, `.pyc`, `environment/repo/.git/logs`.
