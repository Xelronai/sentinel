# Sentinel Ultra — local tooling

Prep Harbor task packages for Experts platform upload. Inspired by the
Terminus/TB3 contribution-repo pattern, but **only** Sentinel Ultra checks.

## Layout

```text
incoming/                 drop downloaded task zips here
tasks/<name>/             working Harbor task (after ingest)
Task_Ready_To_Submit/     flat submission zips (upload these)
scripts/                  checks + package pipeline
.cursor/rules/            Ultra review/submitter rules for agents
```

## Quick start

Requires **Python 3.11+** and **git** on PATH (this machine currently only has the Windows Store Python stub — install real Python from python.org if `python` fails).

```bash
# 1) health check
python scripts/sentinel_doctor.py

# 2) drop a zip into incoming/, then:
python scripts/prepare_task.py incoming/your-task.zip --force

# or prepare an already-extracted tasks/<name>:
python scripts/prepare_task.py tasks/your-task
```

If checks PASS, a zip lands in `Task_Ready_To_Submit/`.
If checks FAIL, fix the reported items (instruction/tests/oracle are human/AI;
hygiene items are often auto-fixed on re-run).

## What this does / does not do

**Does**

- Ingest zip → `tasks/`
- Sync `problem_statement.md` ↔ `instruction.md`
- Rename legacy `solution.patch` / `init_state.patch` → `golden.patch`
- Normalize shell script LF line endings
- Git hygiene checks (+ safe fixes: remotes, `.git/logs`, `base_commit_sha`↔HEAD)
- `task.toml` limits / network_mode
- `fail_to_pass` ≥ 10
- Local **prescriptiveness** check (catches common CodeBuild CDG fails)
- Package **flat** zip without `runs/`, AI scaffolding, caches
- Validate the output zip

Zip-ready instruction rules: `.cursor/rules/sentinel-ultra-zip-ready.mdc`.

**Does not**

- Rewrite instruction tone / expand tests / invent oracle (use Cursor + Ultra rules)
- Edit tracked source under `environment/repo/`
- Run Harbor oracle/NOP (do that on the platform or locally if you have Harbor)
- Replace TB3 collapse/spec/authoring gates

See `commands.md` and `AGENTS.md`.
