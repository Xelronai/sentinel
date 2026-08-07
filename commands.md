# Commands — Sentinel Ultra tooling

Python 3.11+. Run from repo root.

## Doctor

```bash
python scripts/sentinel_doctor.py
python scripts/sentinel_doctor.py --json
```

## Ingest

```bash
python scripts/ingest_zip.py incoming/<task>.zip
python scripts/ingest_zip.py incoming/<task>.zip --name my-task --force
python scripts/ingest_zip.py   # all zips in incoming/
```

## Checks

```bash
python scripts/run_static_checks.py tasks/<task>
python scripts/run_static_checks.py tasks/<task> --json

python scripts/check_git_hygiene.py tasks/<task>
python scripts/check_git_hygiene.py tasks/<task> --fix   # realign base_commit_sha to HEAD
```

`run_static_checks` includes a **prescriptiveness** gate (local stand-in for platform
`cdg_sentinel_ultra.prescriptiveness`). FAIL blocks `prepare_task` from writing a zip.
Rewrite instruction to WHAT-not-HOW, sync `problem_statement.md`, re-run prepare.
Details: `.cursor/rules/sentinel-ultra-zip-ready.mdc`.

## Package / validate

```bash
python scripts/package_task.py tasks/<task>
python scripts/package_task.py tasks/<task> --json

python scripts/validate_submission_zip.py Task_Ready_To_Submit/<task>.zip
```

## One-shot prepare (preferred)

```bash
# zip -> tasks/ -> fixes -> checks -> Task_Ready_To_Submit/
python scripts/prepare_task.py incoming/<task>.zip --force

# already extracted
python scripts/prepare_task.py tasks/<task>

# all incoming zips
python scripts/prepare_task.py --force

# checks only
python scripts/prepare_task.py tasks/<task> --skip-package
```

## Manual Harbor (optional, not gated here)

If Harbor is installed locally:

```bash
# oracle should reward 1.0; NOP should reward 0.0
harbor run ...   # use your local Harbor CLI / docs
```

Platform evals remain the source of truth for difficulty / oracle / quality check.
