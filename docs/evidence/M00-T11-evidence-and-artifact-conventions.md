# M00-T11 — Evidence and artifact conventions evidence

Completed: 2026-09-23

## Invariant

Task evidence must remain short, reviewable, and safe to commit. Generated
benchmarks, evaluations, and reports may be retained locally without making
secrets, private documents, raw production content, or unredacted provider
payloads part of repository history.

## What changed

| File | Change |
|---|---|
| `artifacts/README.md` | Documents the storage policy, categories, retention rule, and prohibited sensitive content |
| `artifacts/examples/harmless-summary.txt` | Retained a harmless synthetic example to make the convention observable |
| `.gitignore` | Ignores generated artifacts while allowing the policy README and harmless example |

The durable evidence path is `docs/evidence/`. Generated working outputs belong
under `artifacts/benchmarks/`, `artifacts/evaluations/`, and
`artifacts/reports/`; private and raw paths remain ignored.

## Verification

The harmless sample is present and intentionally not ignored:

```text
$ git check-ignore -q artifacts/examples/harmless-summary.txt
exit=1
```

A synthetic sensitive path is ignored:

```text
$ git check-ignore -q artifacts/private/synthetic-secret.txt
exit=0
```

The local quality gate passed:

```text
$ make check
All checks passed!
0 errors, 0 warnings, 0 informations
9 passed, 1 deselected in 0.16s
```

The complete pre-commit suite passed after its end-of-file fixer corrected one
missing final newline in `.gitignore`:

```text
$ make hooks
trim trailing whitespace.................................................Passed
fix end of files.........................................................Passed
check python ast.........................................................Passed
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

No sensitive artifact was created or retained.

## Reflection

- **What complexity did this task hide from its caller?** A contributor can
  choose the correct destination for evidence or generated output without
  rediscovering which material is durable, temporary, or forbidden.
- **Which alternative would make the next change harder, and why?** Allowing
  arbitrary reports beside source and documentation would make review and
  cleanup ambiguous. Named artifact categories and a default-ignore policy keep
  generated output separate from committed project history.
- **What production failure would escape if the negative test were removed?**
  A raw export or provider payload could be committed accidentally and remain
  in Git history. The ignored sensitive-path probe verifies the repository
  rejects that default path.

## Confirmations

- The harmless sample contains only synthetic metadata.
- Sensitive artifact paths are ignored by Git and documented as prohibited.
- Existing evidence remains under `docs/evidence/`.
- No secret, private document, raw production content, or provider payload
  entered this record.
