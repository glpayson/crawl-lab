# Exercise dev tasks

default:
    @just --list

# Run the full test suite
test *ARGS:
    uv run pytest {{ARGS}}

# Type-check everything
typecheck:
    uv run mypy .

# Tests + type checker (the step-verification gate), tee'd to dev/verify.log.
# On success, fires the cross-model judge in the background on this step's
# diff (report -> dev/reviews/ in the primary checkout). JUDGE=off skips it.
verify:
    #!/usr/bin/env bash
    set -euo pipefail
    mkdir -p dev
    echo "==== $(date -u +%Y-%m-%dT%H:%M:%SZ) just verify ====" >> dev/verify.log
    # pytest exit 5 = no tests collected: expected pre-first-test, not a failure
    uv run pytest -q 2>&1 | tee -a dev/verify.log || rc=$?
    [ "${rc:-0}" -eq 0 ] || [ "${rc:-0}" -eq 5 ] || exit "${rc}"
    uv run mypy . 2>&1 | tee -a dev/verify.log
    printf '==== end ====\n\n' >> dev/verify.log
    if [ "${JUDGE:-on}" = "on" ] && command -v pi > /dev/null; then
        nohup "$HOME/.claude/skills/judge-review/scripts/judge.sh" code --cap "${JUDGE_CAP:-10}" > /dev/null 2>&1 &
        echo "judge: launched in background (report -> dev/reviews/)"
    fi

# Second-opinion judge on demand: just judge [--ref BRANCH] [--cap N] ...
judge *ARGS:
    "$HOME/.claude/skills/judge-review/scripts/judge.sh" code {{ARGS}}

# Second-opinion judge for PLAN.md: just judge-plan [--cap N] ...
judge-plan *ARGS:
    "$HOME/.claude/skills/judge-review/scripts/judge.sh" plan {{ARGS}}

# Format and sort imports
fmt:
    uv run ruff format .
    uv run ruff check --select I --fix .

# Lint without fixing
lint:
    uv run ruff check .

# Ad-hoc read-only queries against the fixture DB
explore *ARGS:
    uv run python dev/explore.py {{ARGS}}

# Run the service with auto-reload (adjust module path once the app exists)
serve:
    uv run uvicorn app.main:app --reload
