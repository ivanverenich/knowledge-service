# Generated artifacts

This directory is for outputs produced while developing or evaluating the
service.

## Storage policy

- `docs/evidence/` contains short, committed, redacted task evidence.
- `artifacts/benchmarks/` contains local benchmark summaries.
- `artifacts/evaluations/` contains evaluation-run summaries.
- `artifacts/reports/` contains generated reports intended for local review.
- `artifacts/private/` is reserved for sensitive local material and is ignored.
- `artifacts/raw/` is reserved for raw inputs or provider payloads and is ignored.

Generated artifacts are ignored by default. Commit only small, harmless,
reviewable examples that document a convention.

Never commit:

- credentials, tokens, or secrets;
- private organizational documents;
- raw production content;
- unredacted provider requests or responses;
- dumps containing personal or authorization data.

Before retaining an artifact, remove sensitive content and record only the
minimum metadata needed to reproduce or review the result.
