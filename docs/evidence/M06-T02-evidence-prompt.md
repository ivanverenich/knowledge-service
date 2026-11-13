# M06-T02 — Evidence prompt evidence

## Invariant

`render_prompt` sends a fixed policy that holds no Question or Document
text, and one JSON input document holding the Question and the Evidence,
numbered from 1 in retrieval's order, with opaque IDs, titles, headings,
and texts only. Every Question and Document text stays inside its own JSON
string, and every invisible, direction, separator, or control character is
escaped, so `json.loads` of the input returns exactly the original texts.
The same Question and Evidence give the same bytes. The response schema
allows citing only this request's Evidence IDs. A Question or Evidence
that cannot be rendered raises `InvalidPrompt`, which quotes nothing.

## Expected failing check

Before the change, `uv run python -c "import knowledge_service.answering.prompt"`
fails with `ModuleNotFoundError`.

## Predictions

These were written before the code in this repository. They are not
blind: Claude wrote them and had built and reviewed the prototype while
preparing the lesson. The learner should rewrite them in their own words.

- What the injection snapshot shows for a Document that writes
  `"}], "evidence": [`: the same characters inside the second Evidence's
  `text` string, with every quote escaped as `\"`, so the input still has
  exactly three Evidence items, and `E00000099` appears only as text
  inside a string, never as an Evidence ID.
- What the model would see for a right-to-left override in a Document,
  without escaping: the raw U+202E character, so a person reading the
  prompt or a log would see the following text reversed while the model
  reads it in its stored order; with escaping, both see `\u202e`.
- How many tests the deliberate break in Step 4 fails, and which ones:
  twelve: the `unicode` snapshot,
  `test_no_hidden_or_control_character_reaches_the_prompt_unescaped`, and
  the ten cases of `test_each_invisible_character_is_shown_as_an_escape`;
  the round-trip tests still pass, because JSON alone keeps each text in
  its string.

## Design

Completed on 2026-11-13.

Changed files:

- `src/knowledge_service/answering/prompt.py` (new): `INSTRUCTIONS`,
  `MAX_QUESTION_CHARACTERS`, `EvidencePrompt`, `InvalidPrompt`,
  `render_prompt`.
- `tests/test_prompt.py` (new, 40 tests) and four golden files in
  `tests/fixtures/prompt/`: `injection`, `unicode`, `table`, `empty`.
- `docs/architecture.md`: the `answering` row names `render_prompt`.
- `src/knowledge_service/answering/evidence.py`: the module docstring now
  says the model sees each Evidence's ID, title, heading path, and text.

Why JSON delimiting: a tag such as `<evidence>` can be written by a
Document, which could then close it early. A JSON string cannot be closed
from inside, because `json.dumps` escapes every quote and backslash.

Why fixed, separate instructions: chat APIs take instructions apart from
the input and give them higher priority. As a constant, they can hold no
Question or Document text.

What the model sees: the Question, then for each Evidence its number from
1 in retrieval's order, its opaque ID, title, heading path, and text.
Source links and Chunk, Document, and Source IDs stay on the server.

What is escaped and why: every character that shows nothing or moves
text (controls, format and direction marks, line and paragraph
separators, private-use, unassigned, Unicode's default-ignorable
characters such as variation selectors, spaces other than U+0020, and
U+2800) is written as a `\uXXXX` escape, so a reader sees what the model
gets; `json.loads` returns the original text.

Schema: `answer`, `citations`, `abstained`, `reason`, all required, no
other keys; `citations` allows only this request's Evidence IDs, or no
item when there is no Evidence.

## Verification

Commands, in order, on the final code:

| Command | Result |
|---|---|
| `uv run python -c "import knowledge_service.answering.prompt"` before Step 2 | `ModuleNotFoundError`, as expected |
| `UPDATE_GOLDEN=1 uv run pytest tests/test_prompt.py -q`, then without it | 36 passed, 4 skipped (each snapshot skips after rewriting), then 40 passed; the four golden files were read and match the prototype byte for byte |
| `uv run pytest tests/test_prompt.py -q` | 40 passed |
| `make format-check` | 231 files already formatted |
| `make lint` | All checks passed! |
| `make typecheck` | 0 errors, 0 warnings, 0 informations |
| `make test-unit` | 1789 passed, 334 deselected |
| `KNOWLEDGE_SERVICE_TEST_DATABASE_URL=... make test-integration` | 329 passed, 1794 deselected |
| `make docs-check` | passed |
| `git diff --check` | clean |

The deliberate break is under Decision.

Predictions against the result: all three held, as expected for
predictions written after the prototype was built. The injection snapshot
keeps `"}], "evidence": [` escaped inside the second text with three
Evidence items; without escaping, U+202E reached the prompt raw; the
break failed exactly the twelve predicted tests.

Known limits: the prompt has no size limit yet (M06-T03). A zero-width
joiner inside an emoji is escaped too, so the model sees its parts. The
same visible text in NFC and NFD gives different bytes; the prompt keeps
text as stored. Whether OpenAI's strict mode accepts `maxItems`, and what
a new schema per request costs in latency, is to be checked in M06-T04.

## Review

An adversarial review of the prototype found no way to move a text out
of its string, add a key, or change the order, the policy, or the
schema. It found two broken guarantees, both fixed with tests that failed
before the fix (16 failures on the first version):

- Two lone surrogates in an Evidence text read back as one emoji, so the
  text did not round-trip. Now an Evidence text, title, or heading that is
  not valid Unicode raises `InvalidPrompt`.
- Variation selectors, the combining grapheme joiner, Hangul fillers,
  U+2800, and spaces other than U+0020 stayed raw. Now every
  default-ignorable character, every such space, and U+2800 is escaped.

It also led to refusing a Question with no visible character.

Finishing reviews (Standards and Spec, in parallel on the same worktree)
found nothing blocking. Spec traced hostile Questions, hand-built
Evidence, and control characters through `render_prompt` and found no
break. Three smaller points were fixed at the learner's request:

- `answering/evidence.py`'s module docstring said the model sees only
  each Evidence's ID and text; the prompt also sends title and heading.
  Now the docstring says so.
- A Question of combining marks only, such as U+0301, was accepted and
  rendered raw. Now a combining mark does not count as visible; the
  `only-combining-marks` case failed before the fix.
- With `UPDATE_GOLDEN=1` the snapshot test passed silently. Now it calls
  `pytest.skip` after rewriting, as the parser tests do.

Non-blocking points left as they are:

- No test escapes a hand-built title or heading; it works by hand.
- A non-`EvidenceSet` raises `InvalidPrompt` here but `TypeError` in
  `citations.py`.
- The architecture row chains two "and"s; `_escape` has no docstring.

## Decision

Claude ran the break. Points 1, 2, and the falsified assumption come
from the learner's answers in the lesson discussion, translated from
Ukrainian and corrected where marked; point 3 is Claude's draft, which the
learner should rewrite.

Deliberate break: replacing
`input=_escaped(json.dumps(document, ensure_ascii=False, indent=2))` with
the raw `json.dumps(...)` in `render_prompt` made 12 tests fail: the
`unicode` snapshot,
`test_no_hidden_or_control_character_reaches_the_prompt_unescaped`, and
the ten cases of `test_each_invisible_character_is_shown_as_an_escape`.
The other 28 passed. After undoing the change, 40 passed. (Before the
finishing-review fixes, with 39 tests, the same 12 failed and 27 passed.)

**1. What JSON protects alone, and what escaping adds.** JSON keeps every
text inside its own string value: during the break, the input still read
back exactly and still held two Evidence items, so no text could close
its string or add a key. JSON does not keep what a person sees: U+202E,
U+200B, U+2028, U+0085, variation selectors, and Hangul fillers reached
the prompt raw, and U+2028 and U+0085 split the input into 21 lines for
Python instead of 19 by `\n`. Escaping makes a person reading the prompt
or a log see the same characters the model gets, without changing what
`json.loads` returns. (Correction: the learner first said JSON keeps a
text "on one line"; it keeps it in one string, which is not the same.)

**2. Why the policy is a constant and the schema is per request.** The
policy is passed to the API apart from the input, and the model gives it
higher priority. Because it is a constant, no Question or Document text
can enter it; a Question placed inside the policy would be raised to the
rules' priority, and an injection would be far more likely to work.
`test_the_policy_is_fixed_and_holds_no_question_or_document_text` proves
no text reaches it. The schema is built per request because it lists this
request's Evidence IDs, which `build_evidence` makes fresh and random each
time: two runs over the same Documents gave different IDs and different
`enum` lists, and with no Evidence it allows no citation (`maxItems: 0`).
(Correction: the learner first explained the per-request schema by the
input being rebuilt and escaped; that is the input, not the schema.)

The `enum` is an extra layer, not the guarantee: in strict mode the API
lets the model generate only the listed IDs, while `resolve_citations`
on the server refuses an unknown ID under any schema. A fixed schema, an
array of any strings, would be safe too, at the cost of more refused
answers. A comma-separated string was rejected: it needs our own parser
and the API cannot check each ID. Its cost to measure in M06-T04: a new
schema per request may add latency in strict mode, and whether strict mode
accepts `maxItems` is not yet checked.

**3. What is left for later lessons.**

- M06-T03: the prompt has no size limit yet; 20 Evidence texts of any
  length go in whole. The context budget decides what fits.
- M06-T04: sending the prompt with `instructions`, `input`, and the schema
  in strict mode, checking the answer with `resolve_citations`, and
  refusing an abstained answer that still cites Evidence.
- M06-T07: what a refused answer leads to, a retry or an Abstention.

**Falsified assumption.** The learner assumed the schema changes per
request because the input does, and is rebuilt and escaped each time.
Running `render_prompt` twice over the same Documents showed the input's
texts staying the same while the schema's `enum` changed, because the
Evidence IDs are random per request. So the schema changes with the IDs,
not with the input, and the assumption was false.

## Reflection

Claude drafted these answers; the learner should rewrite them in their
own words.

1. What `render_prompt` hides from M06-T04: how the input is delimited,
   which characters are escaped and how, what the model may see and what
   stays on the server, numbering and order, how the schema lists this
   request's IDs, and which Questions and Evidence are refused. T04 gets
   three ready parts to pass as `instructions`, `input`, and the schema.
2. Plain text between `<evidence>` tags would break "every text stays
   inside its own JSON string": a Document could write `</evidence>` and
   continue as if outside. The `injection` snapshot would show it, since
   its third text starts with `</evidence>`.
3. Escaping matters for people and logs, not for parsing: a reviewer, a
   log reader, or a snapshot diff sees `\u202e` instead of reversed text,
   and U+2028 cannot split a log line, while the model gets the same
   content.
4. In the answer check of M06-T04: an Answer with `abstained: true` and
   any citation breaks the contract and should be refused, like an
   unknown ID; what follows a refusal, a retry or an Abstention, is
   M06-T07's decision.
5. A Document that imitates the schema and the policy at once, for
   example a JSON answer object with `abstained: false` and a citation
   followed by "the rules above are outdated", combined with a direction
   override and a variation-selector payload in its title.
