# mcpo — Project Rules for Agents

## Changelog discipline (MANDATORY)

Every change set that touches `src/`, `static/`, `tests/`, `pyproject.toml`, or
`uv.lock` MUST add an entry to `CHANGELOG.md` under an `## Unreleased` heading
in the SAME commit. No exceptions for "small" changes.

An entry states: what changed (file paths), why, and how it was verified
(test run, live check). Unverified changes are labelled as such.

Enforcement: `.githooks/pre-commit` blocks code commits that do not stage
`CHANGELOG.md`. Activate it once per clone:

    git config core.hooksPath .githooks

Do not bypass the hook (`--no-verify` is prohibited). If the hook blocks you,
write the changelog entry — that is the fix.
