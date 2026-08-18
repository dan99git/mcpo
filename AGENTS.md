# Agent rules — mcpo

Maintainer conventions for this fork.

## Work log (required)
- Daily diary IN THIS REPO: `jobs/YY-MM-DD/diary-YYYY-MM-DD.md`. `jobs/` is gitignored — entries are local working records that stay with this pocket of work, never pushed.
- Historical `.claude/diary-*.md` files are frozen history; new entries go to `jobs/`, never there.
- Entries are concise: what was done, why, decision + reason, what is left, what was verified. Log as work lands, not at session end.

## General rules
- Read a file in full before editing it.
- Never delete repo content: move it to `.archive/YYYY-MM-DD/` (gitignored), preserving relative paths.
- No direct pushes: branch + pull request; the maintainer reviews and merges.
- Verify before claiming done — run a check that could have failed; "it compiles" is not "it works".
- Narrow changes only; do not touch unrelated files.
