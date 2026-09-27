# Repo conventions

## Verification

- The step-verification gate is `just verify` (pytest + mypy, tee'd to
  `dev/verify.log`). Run it via `just verify`, never as separate untee'd
  commands, so the log stays complete.
- Paste verification output verbatim in step summaries (per global
  CLAUDE.md) — never just assert that it passed.

## Cross-model judge

- `just verify` fires a second-model judge in the background on the
  current step's diff; reports land in `dev/reviews/` (in the primary
  checkout, even when run from a worktree). `just judge` / `just
  judge-plan` run it on demand.
- **NEVER set `JUDGE=off`** or otherwise disable the judge unless the
  user explicitly asks. It is a review control on your output; you do
  not get to switch it off.
- Background processes launched from your Bash tool die when the call
  ends, so the justfile's fire-and-forget launch does not survive when
  YOU run `just verify`. Instead: run `just verify` normally, and if it
  passes, immediately launch `just judge` in a SEPARATE Bash call with
  `run_in_background: true`.
- Judge findings are CLAIMS for the user to adjudicate — never
  auto-apply them. Act only on findings the user endorses.

## Git conventions

- Branches: `feat/<slug>` (conventional branch naming).
- Commits: conventional commits (`feat:`, `fix:`, `chore:`, `test:`,
  `docs:`).
- Announce any autonomous deviation loudly in your summary: an
  unplanned fix, a commit the user didn't review, a tool you bypassed.
  Never let those pass silently.

## Data

- Fixture DBs under `fixtures/` are read-only ground truth. Explore via
  `just explore "<sql>"` (or `dev/explore.py`). Never modify a fixture.
- `dev/recon.md` holds the bootstrap-time data profile.
