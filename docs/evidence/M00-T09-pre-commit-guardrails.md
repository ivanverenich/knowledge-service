# M00-T09 — Pre-commit guardrails evidence

Completed: 2026-09-23

## Invariant

Every commit receives the same fast repository checks before it can be created:
syntax and configuration errors, accidental conflict markers, oversized added
files, hardcoded secrets, formatting, linting, strict typing, and deterministic
unit tests must all pass. The hooks must not expose secret values or depend on a
JavaScript toolchain.

## What changed

| File | Change |
|---|---|
| `pyproject.toml` | Added `pre-commit` to the development dependency group |
| `uv.lock` | Locked the pre-commit dependency graph |
| `.pre-commit-config.yaml` | Added standard safety hooks, Gitleaks, and local uv-backed project hooks |
| `Makefile` | Added `hooks-install` and `hooks` entrypoints |
| `README.md` | Documented automatic commit behavior and manual hook execution |

The local hooks delegate to existing Makefile commands, so the pre-commit layer
does not duplicate Ruff, Pyright, or pytest policy. `hooks-install` installs the
Git hook for the current clone; the installation itself is local Git metadata
and is not a repository file.

## Hook contract

- `trailing-whitespace` and `end-of-file-fixer` normalize text files.
- `check-ast`, `check-json`, `check-toml`, and `check-yaml` reject malformed
  source/configuration files.
- `check-merge-conflict` rejects unresolved conflict markers.
- `check-added-large-files --maxkb=1000` rejects oversized additions.
- Gitleaks scans staged changes for hardcoded secrets and redacts detected
  values in output.
- Local hooks run Ruff format checking, Ruff linting, Pyright, and deterministic
  unit tests through the repository Makefile.

## Verification

Commands used `UV_CACHE_DIR=/tmp/uv-cache-rag-project` because the default user
cache is outside the writable workspace.

```text
$ uv run pre-commit validate-config

$ make hooks
trim trailing whitespace.................................................Passed
fix end of files.........................................................Passed
check python ast.........................................................Passed
check json.........................................................Skipped
check toml...............................................................Passed
check yaml...............................................................Passed
check for merge conflicts................................................Passed
check for added large files..............................................Passed
Detect hardcoded secrets.................................................Passed
Check Ruff formatting....................................................Passed
Run Ruff linting..........................................................Passed
Run Pyright..............................................................Passed
Run deterministic unit tests.............................................Passed
```

The hook suite was also run in a temporary isolated Git repository with a
synthetic GitHub-token-shaped value. Gitleaks rejected it with exit code 1 and
redacted the value in its output. No secret value entered the repository or
this record.

The large-file hook was run against a temporary staged 1001 KB file. It rejected
the file with:

```text
oversized.bin (1001 KB) exceeds 1000 KB.
```

Both temporary probes and their repository were removed after verification.

## Reflection

- **What complexity did this task hide from its caller?** A developer runs
  `git commit` and receives one consistent safety gate without remembering all
  syntax, security, size, formatting, typing, and test commands.
- **Which alternative would make the next change harder, and why?** A custom
  shell hook would duplicate tool policy and become another script to maintain.
  Pre-commit provides versioned hook environments while local hooks reuse the
  project-owned Makefile commands.
- **What production failure would escape if the negative test were removed?**
  A credential or accidental large artifact could enter Git history before CI
  runs. The synthetic probes prove both protections fail closed at commit time.

## Confirmations

- `make hooks-install` installed the automatic Git hook for this clone.
- Manual `make hooks` runs all configured hooks across the repository.
- No JavaScript package manager or Husky dependency was introduced.
- No provider payload, private document, or real secret entered the repository.
