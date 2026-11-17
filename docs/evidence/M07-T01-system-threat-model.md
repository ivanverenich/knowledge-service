# M07-T01 — System threat model evidence

## Invariant

`docs/threat-model.md` names the system's assets, actors, trust zones,
entry points, and abuse cases with their mitigations for ingestion,
retrieval, generation, agents, and operations. Every numbered invariant in
`docs/security.md` has exactly one row in its trace, with a control and at
least one test owner: a test pytest collects, or a task not yet completed.
`make docs-check` fails when that stops being true.

## Expected failing check

Before the change, `uv run python -c "from knowledge_service.documentation import validate_threat_model"`
fails with `ImportError`.

## Predictions

Drafted by Claude at the learner's request, after the guide's reference run, so they are not blind; the learner should rewrite them in their own words.

- Which security invariant has no test in the code today, and why: invariant 6, because no agent or MCP tool exists yet; M11-T01, M11-T06, and M11-T14 will add them and their tests.
- Which answer endpoint does not use retrieval at all: `POST /v1/answer`, the M01 walking skeleton, which sends the Question text straight to the model with no Evidence or Citations.
- What `make docs-check` prints if a test named in the trace is renamed: one line naming `docs/threat-model.md`, the invariant, and `no such test: tests/<file>.py::<old name>`, and it exits with an error.
- How many tests in `tests/test_threat_model.py` fail if the check stops reporting an invariant with no trace row: six; every test whose only expected issue is "invariant N is not traced".

## Inventory

Drafted by Claude at the learner's request from the Step 2 searches; the learner should check each line against the code.

CI runs only `make format-check`, `make lint`, `make typecheck`, and `make test-unit`; every `*_integration.py` test runs locally under `make test-integration`.

1. **Eligibility.** Control: `eligible_chunks_predicate` in `persistence/access.py` (available Document, a grant for everyone, the User, or one of their Groups). Tests: `test_vector_search_integration.py::test_only_a_matching_access_grant_makes_a_chunk_eligible` and `::test_a_reader_without_groups_sees_only_public_and_own_grants` (integration, not in CI). The Authorization Context is not yet verified from a token: M07-T04. Authorization happens before data leaves storage.
2. **Inside the query.** Control: the same predicate inside the vector, lexical, and sparse queries, beside ORDER BY and LIMIT. Tests: `test_lexical_search_integration.py::test_a_better_match_the_reader_may_not_see_is_never_returned`, `test_vector_search_integration.py::test_a_nearer_chunk_the_reader_may_not_see_is_never_ranked` (integration). The post-retrieval recheck, `confirm_citations`, exists but nothing calls it. Owners for more: M07-T07, M07-T08.
3. **Revocation before cleanup.** Control: grants, Chunks, and availability in one transaction; a tombstone makes a Document unavailable at once. Test: `test_vector_search_integration.py::test_revoked_and_tombstoned_documents_disappear_at_once` (integration). The revocation race test: M07-T10.
4. **Retrieved content is data.** Control: fixed policy, the Question and Evidence as one escaped JSON document, Citations only from this request's Evidence IDs; a ranking model can only reorder eligible Candidates. Tests: `test_prompt.py::test_the_input_reads_back_as_exactly_the_question_and_evidence`, `test_citations.py::test_a_passage_cannot_choose_its_own_citation`, `test_llm_reranking.py::test_an_obeyed_injection_can_only_reorder_eligible_candidates` (unit, in CI). Injected-document evaluation: M07-T13.
5. **No text in telemetry.** Control: log records hold counts, names, and outcomes. Tests plant a marker and check no record holds it: `test_streaming.py::test_the_stream_record_holds_no_text`, `test_generation.py::test_no_question_document_or_answer_text_reaches_logs_or_reprs` (unit, in CI). Traces and a redaction policy: M07-T12.
6. **Agent and MCP calls re-authorized.** No code: no agent, tool, or MCP server exists. Owners: M11-T01, M11-T06, M11-T14.
7. **No ACL leak through citations, caches, traces, artifacts, or errors.** Controls: Citations only from this request's eligible Evidence; one message for empty, unauthorized, and withdrawn; History withholds what the reader may no longer see; no cache exists. Tests: `test_citations.py::test_an_id_from_another_request_is_unknown_here`, `test_abstention.py::test_empty_unauthorized_and_withdrawn_tell_the_user_the_same_thing` (unit), `test_history_integration.py::test_another_users_conversation_has_no_history_for_the_reader` (integration). Owners for more: M07-T09, M07-T11, M07-T15.

Surprises: nothing calls `confirm_citations` (only a comment in `generation.py` mentions it), and `POST /v1/answer` sends `question.text` straight to the model (`openai_model.py`, `input=question.text`), with no retrieval at all.

## Design

- **The threat model.** `docs/threat-model.md` lists assets, actors, trust zones, and entry points, then one table of abuse cases per area (ingestion, retrieval, generation, agents, operations) with "Mitigation now" and "Still to come" (the owning task), an invariant trace, and known gaps. It describes the code after M06 and says "nothing yet" where nothing protects: `confirm_citations` called nowhere, `POST /v1/answer` without retrieval, no token check, no rate limits.
- **The trace format.** Under `## Invariant trace`, the first table: `Invariant | Control | Test owners`, one row per invariant of `docs/security.md`, every owner in backticks.
- **A test owner** is a test pytest collects, `tests/<file>.py::<test>` or `tests/<file>.py::<Class>::<test>` in a `test_*.py` file, found by parsing the file with `ast` (module-level functions and methods of `Test*` classes), or a task ID that exists and is not completed.
- **The check.** `validate_threat_model(root)` in `documentation.py` returns `ValidationIssue`s and never raises on bad Markdown or test files. Invariants must be written `N. text` numbered from 1; any other list item, a duplicate heading, a missing row, an unknown or duplicate row, a placeholder control, or a bad owner is reported. HTML comments and fenced blocks are skipped. `validate_repository` runs it whenever `docs/security.md` exists, so `make docs-check` enforces it.
- **Docs.** `docs/security.md` points to the threat model in one sentence; the path is written in backticks, because a guide embeds that text in a code block where a relative link would break.

## Verification

- `from knowledge_service.documentation import validate_threat_model`: `ImportError` before, imports now.
- `uv run pytest tests/test_threat_model.py -q`: 42 passed; with `tests/test_documentation_validator.py`, 45 passed.
- Gates: `make format-check`, `make lint`, `make typecheck` clean; `make test-unit` 2362 passed (+42); `make test-integration` 378 passed (unchanged); `make docs-check` passed.
- Deliberate break and the real `make docs-check` failure: see `## Decision`.
- Known limits: the check proves an owner exists, not that it tests the invariant; a test marked to be skipped still counts; CI runs only the unit tests, so the integration test owners and `make docs-check` run locally, though the trace itself is checked by a unit test; the model describes the code after M06, and M07 tasks will change many rows.

## Review

An independent adversarial review checked every claim in the threat model against the code and attacked the check with hostile Markdown and test files. Every named test and task existed, and the endpoints, the "nobody may read it" default, the redacted settings, and the local servers on 127.0.0.1 were described correctly. It found these problems, all fixed; each problem in the check has a test that fails on the first version:

- `confirm_citations` was listed as a control in place, but nothing calls it. Now it is a gap owned by M07-T09.
- `POST /v1/answer` was described like the grounded path, but it sends the Question straight to the model. Now it has its own entry point, abuse rows, and a known gap (M09-T01).
- Every parser was said to remove hidden characters; only the PDF parser does. Now the model says the prompt escapes them.
- Invariant 2 named a benchmark test that does not check the production query. Now it names `test_a_nearer_chunk_the_reader_may_not_see_is_never_ranked`.
- An invariant written `8)`, `8.` with two spaces, indented, or as a bullet passed without a row (`test_an_invariant_in_another_list_shape_is_reported`).
- A second `## Invariants` heading, and rows in a comment, a code block, or a second table, were counted or ignored silently (`test_a_second_invariants_or_trace_heading_is_reported`, `test_a_row_outside_the_visible_trace_table_traces_nothing`).
- A "test" that was text, dead code, nested, in a non-`Test*` class, or in a file pytest does not collect passed (`test_a_test_owner_must_be_a_test_pytest_collects`, `[not-collected-file]`).
- A test file that was not UTF-8 or not valid Python crashed the check (`test_a_test_file_that_cannot_be_read_is_reported_not_raised`).
- A control of `TBD` or `-` passed (`[placeholder]`, `[dash]`).
- Missing abuse rows were added: a cache shared across readers, parser resource exhaustion, provider retention, History and rewriting carrying another User's text, unauthenticated access.

Documented, not fixed: a skipped test still counts; CI runs neither the integration owners nor `make docs-check`.

## Decision

Drafted by Claude at the learner's request; the learner should rewrite it in their own words.

- **Checked by code, not only reviewed.** A trace goes stale without anyone noticing: a test is renamed, a planned task is finished, a new invariant is added. A reviewer reads the table once; `make docs-check` and the unit test `test_the_repository_threat_model_traces_every_security_invariant` read it on every run, so drift fails at once.
- **A completed task may not stay a test owner.** "M07-T10 will add the test" is a promise. Once M07-T10 is completed, the promise is either kept, and its row must name the test, or broken, and the row would claim protection that does not exist. Failing the check forces one of the two to be written down.
- **Only what a reader sees counts.** The trace is for people. A row hidden in a comment, a code block, or a second table below would satisfy the check while a reader sees an untraced invariant, so it would make the check lie. The same goes for a "test" pytest never runs: text in a string or a dead branch proves nothing.
- **Invariants with no test in the code today.** Invariant 6 has none: no agent, tool, or MCP server exists; M11-T01, M11-T06, and M11-T14 own it. The others have tests, but parts still lack one: the verified Authorization Context (M07-T04), the revocation race (M07-T10), injected-document evaluation (M07-T13), trace redaction (M07-T12), and citation rechecks in a served path (M07-T09).
- **Left for later.** The check proves an owner exists, not that it tests the invariant. A skipped test still counts. CI runs neither the integration owners nor `make docs-check`.

**Falsified assumption.** I assumed `confirm_citations` already protected the served Answers, because it was built and tested in M06. The review shows nothing in `src/` calls it. So "a late revocation never reaches the client" was wrong for today's code; the model lists it as a gap owned by M07-T09.

**Break run.** Without the block that reports untraced invariants: 6 failed, 36 passed (`test_an_invariant_added_without_a_trace_row_is_reported`, `[malformed]`, the three `test_a_row_outside_the_visible_trace_table_traces_nothing` cases, and `test_the_documentation_check_runs_the_trace`). Restored: 42 passed.

**Real failure.** With `test_the_stream_record_holds_no_text` renamed to `..._texts` in the trace, `make docs-check` printed `docs/threat-model.md: invariant 5: no such test: tests/test_streaming.py::test_the_stream_record_holds_no_texts` (after the repository's absolute path) and exited with an error. Undone, it passes.

## Reflection

Drafted by Claude at the learner's request; the learner should rewrite it in their own words.

1. It does not prove that the test checks the invariant, that it runs in CI, or that it is not skipped. A test named `test_x` could assert nothing about permissions. The author of the row and the reviewer have to judge that by reading the test.
2. A Document revoked after retrieval but before the Answer is shown: the Answer cites it, and the User sees its title and link and a claim drawn from it, though they may no longer read it. Today the window is short, but nothing closes it until M07-T09.
3. Invariant 1 or 2: a cache of search results keyed only by the Question would serve one reader's Candidates to another. `test_a_reader_without_groups_sees_only_public_and_own_grants` would not catch a cache in front of the query; the adversarial suite (M07-T15) must test with two readers asking the same Question.
4. Helpful: it is the moment the promise "this task adds the test" is checked. If completed tasks could stay owners, the trace would slowly fill with task IDs that look like protection but point at nothing.
5. `POST /v1/answer` and `POST /v1/answer/stream` without a token check: anyone who reaches the API gets Answers. The smallest change is to require a verified token on both routes (M07-T03) and build the Authorization Context from it (M07-T04), or to remove the ungrounded `POST /v1/answer` until M09-T01.
