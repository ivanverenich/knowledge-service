# M07-T12 — Redact logs and traces evidence

## Invariant

Every log record the service exports passes one filter: only allowlisted
fields with plain values, only the service's own messages, and only an
exception's type. Raw Questions, Evidence, and Answers appear in exported
telemetry only when development diagnostics are on, and production refuses
them however the settings were made. With diagnostics off, no record the
service creates holds that text at all.

## Expected failing check

Before the change, `uv run pytest tests/test_telemetry.py -q` fails with
`ERROR: file or directory not found: tests/test_telemetry.py`.

## Predictions

Drafted by Claude at the learner's request, after the guide's reference run, so they are not blind; the learner should rewrite them in their own words.

- What the exported line for an answered stream holds, and whether the Question's words appear in it: one JSON line, "answer stream ended", with `answer_stream` holding counts, outcome, phase, timing, and tokens, and no `redacted_fields`; no word of the Question, Evidence, or Answer appears.
- What happens to `extra={"answer": {"outcome": "a whole sentence", "question": "..."}}`: `outcome` stays as `[redacted]`, `question` is dropped because it is not allowlisted, and the line gets `redacted_fields: 1`.
- What happens when another library logs a message with a Question built into it: the message becomes `[redacted]`; the logger, level, and exception type stay.
- Which test fails if the filter stops redacting other libraries' messages: only `test_another_librarys_message_is_redacted` (1 failed, 9 passed).

## Design

- **One exit.** `configure_telemetry` installs one `TelemetryHandler` on the root logger, so every record that propagates, from the service's modules and from libraries, leaves through it as one JSON line (`TelemetryFormatter`: time, level, logger, message, kept fields). Module code is unchanged: it still calls `logger.info(...)`.
- **Allowlist and value rule.** `TelemetryFilter` keeps only the `extra` names in `TELEMETRY_FIELDS`, and of each only the listed keys, which are the keys the modules log today; other keys are dropped and counted in `redacted_fields`. A kept value must be None, a bool, an int, a finite float, or a word of at most 128 ASCII letters, digits, and `_ . : / -`; anything else becomes `[redacted]`. The same rule cleans the tuple arguments of the service's own messages; a mapping argument keeps only the template.
- **Other libraries and exceptions.** A message from a logger outside `knowledge_service` becomes `[redacted]`, because it may have text built into it. An exception keeps only its type, with no traceback or stack. Ruff's `G` rules keep the service's own messages as templates with separate arguments.
- **A cleaned copy.** The filter returns a new record (Python 3.12+), so other handlers see the original; if cleaning fails, it returns "telemetry record dropped" instead of raising into the caller.
- **Diagnostics at the source.** `Settings.telemetry_diagnostics` (off by default) lets the answer stream add `diagnostic` with the Question, the allocated Evidence texts, and the streamed Answer; `_Span` adds it only when `diagnostics_enabled()`, which asks the installed handler. The first line then warns that raw text is logged. Production refuses it in `Settings` and again in `configure_telemetry` (`DiagnosticsNotAllowed`).

## Verification

- Expected failing check: before the change, `uv run pytest tests/test_telemetry.py -q` failed with `ERROR: file or directory not found: tests/test_telemetry.py`.
- After Step 2, `uv run ruff check .` passed with the `G` rules on and `tests/test_settings.py` passed (12); after Step 3, pyright reported 0 errors; after Step 4, `tests/test_streaming.py` passed unchanged (62); after Step 5, `tests/test_telemetry.py`: 10 passed.
- Deliberate break: without the `_own(record.name)` check, 1 failed, 9 passed (`test_another_librarys_message_is_redacted`). Restored: 10 passed. The prediction was right.
- Gates: `make format-check`, `make lint`, `make typecheck` clean; `make test-unit` 2795 passed (+10); `make test-integration` 641 passed (unchanged); `make docs-check` passed.
- Known limits: no entry point calls `configure_telemetry` yet; loggers with `propagate=False`, such as uvicorn's, skip the root handler; a single plain word in an allowlisted field passes the value rule; traces must follow the same rules when they arrive (M10-T14).

## Review

An independent adversarial review attacked the first version with hostile fields, message arguments, exceptions, odd types, other loggers, and setting tricks, and ran ten mutations. Fixed, each with a test that fails on the first version:

- asyncio's "Task exception was never retrieved" carried an exception's text inside the message itself. Now messages from other libraries are redacted whole.
- A single dict argument let its keys through. Now a mapping argument keeps only the template.
- Settings changed after validation (`model_copy`, assignment) could turn diagnostics on in production. Now `configure_telemetry` raises `DiagnosticsNotAllowed`.
- Removing the handler by hand left a global diagnostics switch on. Now `diagnostics_enabled()` asks the installed handler.
- Nine of ten mutations survived the first tests (Ukrainian words passing, no length cap, mapping arguments, stack kept, traceback kept, original record mutated, a narrowed `except`, `diagnostic` kept when off, a second handler). All are now killed; the one left (`str()` instead of `str.__str__`) is equivalent, since it still redacts.

Documented, not fixed: nothing calls `configure_telemetry` yet; loggers that do not propagate; single plain words; other root handlers a later setup might add.

## Decision

Drafted by Claude at the learner's request; the learner should rewrite it in their own words.

- **An allowlist.** A search for secret-looking text misses what nobody predicted. Naming what may leave means a new field is hidden until someone decides it is safe; a missing entry costs telemetry, never secrecy.
- **A filter on the root handler, not a wrapper.** A wrapper would have to be used everywhere and would never see other libraries' records. Every logger hands its records to the root, so one handler sees them all without changing module code.
- **Other libraries' messages redacted whole.** Their text is already built in when it arrives, so it cannot be cleaned piece by piece; their logger, level, and exception type are still enough to notice a problem.
- **Diagnostics refused twice.** `Settings` validates once, but settings objects can be changed or copied afterwards; the place that turns diagnostics on is the last chance to refuse them.

**Falsified assumption.** I assumed that cleaning the arguments of every message was enough to keep text out of exported messages. The review shows that asyncio builds an exception's text into the message itself before logging it, so it passed untouched. So "messages are templates and only arguments carry data" is wrong for other libraries; only the service's own messages, kept as templates by ruff's `G` rules, can be cleaned that way.

## Reflection

Drafted by Claude at the learner's request; the learner should rewrite it in their own words.

1. The exported line has no `question` in `answer` and shows `redacted_fields: 1`. They would notice when the field they expected is missing; a test of the exported line, like `test_an_answer_exports_its_counts_and_none_of_its_text`, which requires no `redacted_fields` from the service's own records, would fail.
2. The message arrives already built, so the filter cannot tell which part is a Question; looking for it would only find text someone predicted. Redacting the whole message is the only rule that cannot miss.
3. `failure` or `reason` could carry a word if someone put a document word there, and `model` comes from configuration. A rule that these fields take only values from a fixed list (an enum) would stop it.
4. A mistake after validation: code that copies settings with `model_copy(update=...)`, assigns the field, or builds settings with `model_construct`, none of which runs the validator.
5. `TELEMETRY_FIELDS` and `plain` for attribute names and values, and the rule that raw text appears only under diagnostics. The first test would run an answered stream through an in-memory span exporter and scan every attribute for the seeded sentence.
