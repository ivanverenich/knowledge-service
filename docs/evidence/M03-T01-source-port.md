# M03-T01 — Source port evidence

Completed: 2026-10-13

## Invariant

Ingestion reads from a Source only through the Source port, so it can page
through records and resume from a checkpoint without knowing where those records
are stored. A record's `external_id` is stable across deliveries, so the same
item can be recognised when a Source delivers it more than once, and a deletion
arrives as an explicit signal rather than as a record that is simply absent.
The port hides pagination, the checkpoint format, the raw access payload, and
the adapter's failure modes from its caller; the caller sees pages of records
and a typed error.

## Prediction

The fake Source exercises five behaviors. This is what I expect the caller to
observe in each case, written before the fake and its tests exist.

- **Multiple pages.** Each call returns one page of records in the Source's
  order. `next_checkpoint` is a string until the last page, and `None` on the
  last page, which is the only signal that the Source is exhausted.
- **Resume.** Passing the previous page's `next_checkpoint` returns only the
  records that follow the delivered ones. The fake does not re-deliver earlier
  pages, but a real Source may, which is why identity has to be stable.
- **Duplicate delivery.** The same `external_id` appears in two pages as two
  equal records. The caller must treat the second as the same item, not as a new
  Document, because at-least-once delivery is the norm.
- **Deletion.** A deleted record keeps its identity and version but has
  `state == DELETED`, `content is None`, and `access is None`. It is enough to
  tombstone the item, and it carries no payload to normalize.
- **Failure.** `fetch_page` raises `SourceUnavailable`, a `SourceError`, rather
  than a bare exception, so the caller can treat an unreachable Source as a
  retryable condition instead of an internal defect.

## Design

Changed files:

- `src/knowledge_service/sources.py` — added the Source port next to the
  existing `Source` and `SynchronizationRun` values, the same way `model.py`
  keeps `ChatModel` next to its errors.
- `tests/fakes.py` — added `FakeSource`, a deterministic in-memory adapter.
- `tests/test_source_port.py` — new tests at the port's interface.

The contract:

| Name | Role |
|---|---|
| `SourceAccess` | Raw access payload: a `public` flag or a tuple of unique, non-empty `principals`, never both. Not the normalized `Subject`; normalization belongs to M03-T06. |
| `SourceRecordState` | `present` or `deleted`. |
| `SourceRecord` | One raw item: `external_id`, optional `version`, `state`, and for a present item `access`, `media_type`, and `content`. A deleted record carries identity only. Built through `present(...)` and `deleted(...)`. |
| `SourcePage` | One page of records plus an opaque `next_checkpoint`; `None` means the Source is exhausted. |
| `SourcePort` | A `Protocol` with one method, `fetch_page(*, checkpoint, page_size)`. `checkpoint=None` starts from the beginning. |
| `SourceError` → `SourceUnavailable`, `SourceUnauthorized` | Typed runtime failures an adapter raises. |

Two error families stay separate on purpose. `InvalidSource` is a broken value,
which is a programming error. `SourceError` is an expected runtime condition,
such as an unreachable Source, which the caller can retry or report.

`FakeSource` serves an explicit list of pages. Its checkpoint is the page index
as a string, failure is injected by call number, and duplicate delivery is
modelled by placing the same record in two pages. It ignores `page_size`
because it serves whole pages; it accepts the argument so it still satisfies
`SourcePort`. The public contract names no provider, path, URL, or storage
detail, and no credential or private content enters any record or artifact.

## Verification

Run on 2026-10-13 with `UV_CACHE_DIR=/tmp/uv-cache-rag-project`:

| Command | Result |
|---|---|
| `uv run pytest tests/test_source_port.py -q` | 6 passed |
| `make format-check lint typecheck` | 76 files already formatted; Ruff: all checks passed; Pyright: 0 errors, 0 warnings |
| `uv run pytest -m "not integration and not live" -q` | 298 passed, 20 deselected |
| `make check` | 299 passed, 17 skipped, 2 deselected; format, lint, and types clean |
| `make docs-check` | exit 0 |
| `git diff --check` | silent |

The first narrow run failed: `test_a_source_failure_is_typed` reported
`DID NOT RAISE SourceUnavailable`. `FakeSource.fetch_page` never recorded the
checkpoint, so its call count stayed at zero and `fail_on_call=1` never fired.
Adding `self.checkpoints.append(checkpoint)` as the first line fixed it. The
negative test caught a real defect in the fake.

Pyright passing proves `FakeSource` structurally satisfies `SourcePort`: the
first test assigns it to a variable typed `SourcePort`.

## Reflection

1. **What does one page plus a checkpoint force the caller to handle?** The
   caller has to loop until `next_checkpoint` is `None` and store the checkpoint
   between calls. A single list would hide that the read can stop halfway:
   a failure on page three would lose the first two pages' progress, and the
   whole Source would have to fit in memory. Paging makes progress, resume,
   and partial failure visible.
2. **What gets harder without checkpoints or a stable `external_id`?** The
   reconciliation tasks: M03-T12 (reconcile a Document transactionally) and
   M03-T13 (reconcile deletions with tombstones). Without a stable
   `external_id`, the same file delivered twice looks like two Documents, so
   the upsert is no longer idempotent, and a deletion cannot say which Document
   to tombstone. Without a checkpoint, every run re-reads the whole Source and
   a failed run cannot resume, which also makes M08-T06 (incremental syncs)
   harder later.
3. **What escapes if `test_a_source_failure_is_typed` is removed?** This lesson
   already showed it: without that test, the fake's broken failure injection
   went unnoticed. In production, an adapter could raise a bare exception or
   swallow the error and return an empty page. An empty page reads as "the
   Source has no items", which could tombstone every Document instead of
   retrying later.
