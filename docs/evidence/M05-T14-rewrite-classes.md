# M05-T14 — Rewrite classes evidence

## Invariant

`classify_question` puts every Question in exactly one rewrite class and
returns the words any rewrite must keep, as typed, with the stated
language. A Question with any word that holds a digit, or whose parts
are joined by `_`, `@`, `/`, `.`, `:`, or a backslash, is
`exact_identifier` and never rewritable, and no separator inside a word
can split that word into rewritable pieces. Every verified case carries a
class label and its words to keep, and the loader refuses a case whose
label could not be checked. Errors, reprs, and the report never hold a
Question or a word of one. Nothing is rewritten yet.

The joiner clause was added after the finishing Spec review (see Review);
the invariant first said only "a digit".

## Expected failing check

Before the change, `uv run python -c "import knowledge_service.retrieval.rewrite_classes"`
fails with `ModuleNotFoundError`. Confirmed in this repository before
Step 2.

## Predictions

These were written before the run in this repository. They are not blind:
Claude wrote them and had seen the prototype pass while preparing the
lesson.

- How many of the 16 seed questions are `self_contained`, and which are
  not: 11 are `self_contained`; the other 5 are four `exact_identifier`
  questions (`en-en-error-exp-2041`, `en-en-error-vpn-4031`,
  `uk-uk-form-it-0457`, `uk-en-error-vpn-4031`) and one `acronym`
  question (`en-en-vpn-first-connection`), because no seed question has
  an earlier question and none mixes alphabets.
- Whether the rules agree with every label of the 29 cases on the first
  run: yes, all 29 agree on class and on words to keep, and the script
  exits 0, because the labels were checked against the same rules.
- One Question a rule will get wrong if the identifier rule needed a
  letter and a digit: the follow-up in `en-follow-up-number`, whose only
  identifier is a bare number, becomes a rewritable `follow_up` instead
  of `exact_identifier`.
- Which class M05-T15 should rewrite first, and why: `follow_up`,
  because a short question that leans on an earlier one cannot find
  anything on its own, so a rewrite helps it most, while acronyms and
  foreign words often already match the documents as typed.

## Design

Completed on 2026-11-11.

Changed files:

- `src/knowledge_service/retrieval/rewrite_classes.py` (new):
  `RewriteClass`, `REWRITABLE`, `RewritePlan` with `may_rewrite`,
  `InvalidQuestion`, `question_words`, and `classify_question`.
- `src/knowledge_service/retrieval/rewrite_cases.py` (new):
  `load_rewrite_cases`, `RewriteCase`, `RewriteCaseSet`,
  `ClassAgreement`, `plan_case`, `score_rewrite_cases`, and
  `render_rewrite_scores`.
- `datasets/bilingual-seed/rewrite-classes.json` (new): 33 labelled
  Questions, the 16 seed questions by ID and 17 new ones.
- `scripts/score_rewrite_classes.py` (new): writes
  `artifacts/evaluations/rewrite-classes.md` and exits 1 when any case is
  missed.
- Tests: `tests/test_rewrite_classes.py` (56) and
  `tests/test_rewrite_cases.py` (25), both new.
- `docs/architecture.md`: the `retrieval` row.

Five classes, checked in this order; the first that holds decides:
`exact_identifier`, `follow_up`, `acronym`, `cross_language`,
`self_contained`. One label per Question keeps the measurement simple:
right or wrong.

Why identifiers win, and why the rule is broad: a missed identifier costs
the user the answer, while a missed rewrite costs only a little recall.
So any number character counts (`2041`, `10.0.0.1`, a Roman numeral), and
so does a joiner inside or at the start of a word (`ERR_TIMEOUT`,
`hr@example.com`, `/etc/hosts`, `.env`). A plain hyphen does not count,
because "Wi-Fi" is an ordinary word.

What a word is: a run of characters between spaces, with punctuation
trimmed from the ends, except a joiner at the start and `_` at the end.
Only spaces split words, so no dash, underscore, or invisible joiner can
cut an identifier into a rewritable acronym. A full stop or "..." after
the last word is still trimmed.

Why words to keep are whole words: a substring is not enough. A rewrite
that keeps `EXP` could still drop `2041`.

Why labels do not depend on the rules: the loader checks only what a
person can check (whole words, listed once, an `exact_identifier` case
keeps at least one word, a `follow_up` case has history). Which word is
right is decided by scoring, which demands the exact list, in order. A
rule that keeps a word too many or too few misses the case.

Why a separate labelled file: `cases.json` drives every retrieval report
since M05-T07, and follow-ups would change all of them. The labelled file
reuses the 16 seed questions by ID, and a test checks that their text and
language still match the seed.

Not touched: no stored Documents, Chunks, or Candidates, no database, no
model calls, and no change to retrieval. Nothing is rewritten yet.

## Verification

Commands, in order, on the final code:

| Command | Result |
|---|---|
| `uv run pytest tests/test_rewrite_classes.py tests/test_rewrite_cases.py -q` | 81 passed |
| `make format-check` | 215 files already formatted |
| `make lint` | All checks passed! |
| `make typecheck` | 0 errors, 0 warnings, 0 informations |
| `make test-unit` | 1538 passed, 317 deselected |
| `KNOWLEDGE_SERVICE_TEST_DATABASE_URL=... make test-integration` | 313 passed, 1542 deselected |
| `uv run python scripts/score_rewrite_classes.py` | every class fully agreed, none missed, exit 0 |
| `make docs-check` | passed |
| `git diff --check` | clean |

Predictions against the result:

- Seed questions: right. 11 of 16 are `self_contained`, and the other
  five are the four identifier questions and one acronym question.
- Agreement on the first run: right for the 29 cases that existed then.
  The finishing reviews added four cases (33 in all), and all of them
  agree too.
- The letter-and-digit rule: right. `en-follow-up-number` was missed, and
  after the review fixes the same break also misses four more cases.
- Which class to rewrite first: not tested here; M05-T15 measures it.

Known limits: 33 cases are few. The follow-up words are a fixed list, so
"And for contractors?" with four words is missed. Any number blocks
rewriting, even "5 days", and so does a dotted abbreviation such as
"e.g.", a word that starts with "...", or emphasis like `_really_`. A
word like `VPN-client` is neither an acronym nor foreign in an English
Question. Fullwidth `／` and `＿` are not joiners. `VPN's` is not seen as
an acronym. A short new Question after an earlier one counts as a
follow-up.

## Review

An adversarial review of the prototype, before the guide was written,
found two broken guarantees and two smaller problems. All are fixed, each
with a test that failed before the fix:

- Words were split at dashes and underscores, so `EXP_2041` or
  `EXP–2041` became a rewritable acronym `EXP`. Now only spaces split
  words.
- Digits alone, such as `2041` or `10.0.0.1`, were not identifiers, so a
  follow-up naming them was rewritable. Now any number character counts.
- A labelled Question over 1,000 characters loaded and then crashed the
  script. Now the loader runs every case through `classify_question`.
- A `str` subclass or a lone surrogate escaped as another error type. Now
  only plain, valid `str` is accepted.

Finishing reviews (Standards and Spec, run in parallel on the same
worktree):

- Standards: nothing blocking. Non-blocking points taken: the
  `classify_question` docstring now lists its refusals clearly, and
  `_other_alphabet` names its loop variable `character`. Left as is:
  `lexical_routing.question_script` still has its own digit and alphabet
  checks, and `history` stays annotated `Sequence[str]` while the code
  accepts only a list or tuple (pyright rejects the runtime check against
  a narrower annotation, and the check is tested).
- Spec, blocking: an identifier without a digit, such as `ERR_TIMEOUT`,
  `hr@example.com`, or `/etc/hosts`, was a rewritable follow-up with
  nothing kept. Now `_`, `@`, `/`, `.`, `:`, or a backslash in a word
  makes it an identifier. Labelled cases `en-follow-up-error-code`,
  `en-follow-up-address`, and `uk-path-in-question` were added, and the
  new tests failed 10 times on the code before the fix.
- Spec, non-blocking, taken: scoring checked the words to keep as a
  subset, so a rule that kept too many words passed. Now it demands the
  exact list. The loader check that an `exact_identifier` label keeps "a
  word with a digit" was replaced by "at least one word", so labels no
  longer depend on the rules they measure; otherwise the deliberate break
  made the loader refuse the file instead of naming the missed cases.
- Spec re-review, blocking: a joiner at the start of a word was trimmed
  before the check, so `.env`, `/tmp`, `@oncall`, or `__init__` were
  still rewritable. Now a joiner at the start and `_` at the end of a word
  are kept. Labelled case `en-follow-up-dotfile` was added, and the new
  tests failed 10 times on the previous code.
- A second re-review found nothing blocking; it named the over-protection
  listed under Known limits.

## Decision

Report from `uv run python scripts/score_rewrite_classes.py` (exit 0),
copied here because `artifacts/` is ignored by Git:

| Class | Cases | Class assigned | Words kept | May rewrite | Missed |
|---|---:|---:|---:|---|---|
| exact_identifier | 10 | 10 | 10 | no | none |
| follow_up | 4 | 4 | 4 | yes | none |
| acronym | 4 | 4 | 4 | yes | none |
| cross_language | 3 | 3 | 3 | yes | none |
| self_contained | 12 | 12 | 12 | no | none |

What M05-T15 may rewrite:

- `follow_up`: yes. A short Question that leans on an earlier one cannot
  find anything on its own; a rewrite can carry the earlier topic in.
- `acronym`: yes. A rewrite may add the spelled-out form that documents
  use, while the acronym itself stays.
- `cross_language`: yes. A rewrite may add the same words in the
  Question's language, so documents in either language can match.
- `exact_identifier`: no. Changing one character of an identifier finds
  nothing, and a rewrite has nothing to add to an exact lookup.
- `self_contained`: no. The Question already searches well as typed
  (11 of the 16 seed questions), so a rewrite can only cost time and risk
  drift.

Constraints M05-T15 must check on every rewrite, and fall back to the
Question as typed when one fails:

- every word in `preserve` is present in the rewrite as a whole word from
  `question_words`, exactly as typed;
- the rewrite's language is the Question's stated language, unchanged.

Deliberate break: replacing the body of `_is_identifier` with "a letter
and a digit" made 17 tests fail: four separator cases (`2041`,
`10.0.0.1`, `#2041`, the Roman numeral), all eleven cases of
`test_a_code_address_or_path_without_digits_is_an_identifier`,
`test_the_rules_agree_with_every_verified_label`, and
`test_the_report_names_cases_by_id_only`. The script named
`en-follow-up-number`, `en-follow-up-error-code`, `en-follow-up-address`,
`uk-path-in-question`, and `en-follow-up-dotfile` as missed and exited 1:
"And error 2041?" after a question about `EXP-2041` became a rewritable
`follow_up`. After undoing the change, 81 passed and the script exited 0.
Before the review fixes, the same break gave 6 failures and named only
`en-follow-up-number`.

Falsified assumptions:

- I assumed, as an earlier draft of the rules did, that an identifier
  always holds a letter and a digit. The run shows a bare number is an
  identifier too (`en-follow-up-number`), and the break would have let a
  rewrite touch it. So "a letter and a digit" is wrong; any numeric
  character makes a word an identifier.
- I assumed that every identifier holds a digit. The Spec review showed
  `ERR_TIMEOUT`, `hr@example.com`, and `.env` (`en-follow-up-error-code`,
  `en-follow-up-address`, `en-follow-up-dotfile`) are identifiers with no
  digit at all, and the rules made them rewritable. So "a digit" is not
  the mark of an identifier; how its parts are joined is one too.

## Reflection

Claude drafted these answers; the learner should rewrite them in their
own words.

1. What `classify_question` hides from M05-T15: what a word is (spaces
   only, which punctuation is trimmed and which joiners stay), which
   characters make an identifier, the order of the five rules, the word
   lists that point back, how alphabets are told apart through Unicode
   names, the input limits, and that errors never quote the Question. A
   caller gets one plan: may it rewrite, and which words to keep.
2. Dropping the identifier rule for follow-ups would break "a Question
   with an identifier is never rewritable". `test_the_first_rule_that_holds_decides`
   and `test_a_question_with_an_identifier_is_never_rewritten` would
   fail, and the script would name `en-follow-up-keeps-identifier`,
   `en-follow-up-number`, `en-follow-up-error-code`,
   `en-follow-up-address`, and `en-follow-up-dotfile`.
3. After "How many days of leave do I get?", "And for contractors?" has
   four words and no word that points back, so it is `self_contained` and
   searched as typed, which finds little. I would first add it as a
   labelled `follow_up` case so the script shows the miss, then decide
   between a longer length limit and treating "And ..." as pointing back,
   by how many other labelled cases each choice changes.
4. The prediction that held least was "all 29 agree": it was right, but
   only because the labels and the rules came from the same prototype.
   It said nothing about identifiers without digits, which the Spec
   review found. Agreement with labels I wrote myself is weak evidence.
5. Before M05-T15 rewrites anything for real users: measure MRR per
   class, with and without rewriting, on the `follow_up` cases, plus how
   often a rewrite is refused for dropping a word to keep. A case that
   could prove me wrong is `en-follow-up-abroad`: if its rewrite does not
   raise the expected Document's rank, rewriting follow-ups does not pay.
