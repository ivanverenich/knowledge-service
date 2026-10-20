# M03-T07 — Language metadata evidence

Completed: 2026-10-19

## Invariant

The same blocks always give the same `LanguageProfile`. A profile is either
`unknown` with a confidence of 0, or a known label (`en`, `uk`, or `mixed`)
with a confidence above 0 and at most 1. Only the visible text of non-code
blocks counts: code blocks and HTML markup never change the label. The label
is descriptive metadata and never an authorization decision, so a Document's
access is the same whatever language its text is in.

## Prediction

Written before the detector and its tests exist. This is the label and rough
confidence I expect for each text.

- **A long English paragraph.** `en`, with confidence 1.0: almost every
  letter is Latin, and the paragraph has more than 200 letters.
- **A long Ukrainian paragraph.** `uk`, with confidence 1.0: almost every
  letter is Cyrillic, and letters such as `є` and `ї` show it is Ukrainian.
- **The two paragraphs together.** `mixed`: neither script reaches 80% of the
  letters. Confidence is 1.0, because every letter is Latin or Cyrillic and
  there are more than 200 of them.
- **A Ukrainian paragraph with a few English terms such as API and
  Kubernetes.** Still `uk`, because Cyrillic stays above 80%. Confidence is a
  little below 1.0, around 0.9, because the English letters do not support
  the label.
- **"Hello, team!"** `unknown` with confidence 0: it has only 9 letters,
  fewer than the 20 needed to decide.
- **A 40-letter Ukrainian sentence.** `uk` if it contains `ґ`, `є`, or `ї`,
  with a low confidence of about 0.2, because 40 letters is a fifth of the
  200 needed for full confidence.
- **A Russian paragraph.** `unknown`: letters such as `ы` and `ё` are outside
  the Ukrainian alphabet and outnumber the Ukrainian-only letters, so it is
  not labelled `uk`.
- **A Kazakh sentence.** `unknown`: it uses the dotted `і` but no `ґ`, `є`, or
  `ї`, and it has letters such as `ү` and `ө` from outside the Ukrainian
  alphabet.
- **Chinese text.** `unknown`: most of its letters are from another script.
- **A Ukrainian paragraph followed by a large Python code block.** `uk`: the
  code block is skipped, so its English keywords do not count.

## Design

Changed files:

- New `src/knowledge_service/language.py`: `Language` (`en`, `uk`, `mixed`,
  `unknown`), `LanguageProfile`, `InvalidLanguageProfile`, and
  `detect_language`.
- `src/knowledge_service/normalization.py`: `NormalizedDocument` gains a
  `language` field, which `normalize` sets from the parsed blocks.
- New `tests/test_language.py` (24 tests, counting parametrized cases) and two
  new tests in `tests/test_normalization.py` (27 in total).

The policy counts letters by script in the text of non-code blocks:

| Rule | Result |
|---|---|
| Fewer than 20 letters | `unknown` |
| More than 20% of letters from another script | `unknown` |
| At least 80% Latin | `en` |
| At least 80% Cyrillic, with `ґ`, `є`, or `ї` present and outnumbering Cyrillic letters from outside the Ukrainian alphabet | `uk` |
| At least 80% Cyrillic otherwise | `unknown` |
| Anything else | `mixed` |

Confidence is the share of letters that support the label times
`min(1, letters / 200)`, rounded to two decimals. `unknown` always has 0, and
`LanguageProfile` refuses any other combination with `InvalidLanguageProfile`.

Decisions:

- **A letter-counting heuristic, not a detection library.** The corpus has two
  languages in two scripts, so counting letters separates them. The rule is
  deterministic, needs no dependency or model file, runs in linear time (about
  0.1 seconds for 1,000,000 characters), and every result can be explained.
- **Blocks, not `ParsedDocument.text`.** For HTML, `text` is the raw source,
  whose tag and attribute names are English. Only block text is counted, and
  code blocks are skipped because code is written in English keywords.
- **Ukrainian needs positive evidence.** `ґ`, `є`, and `ї` belong to Ukrainian
  alone; the dotted `і` is shared with Belarusian and Kazakh, so it is not
  evidence. Letters outside the Ukrainian alphabet (Russian `ы`, Serbian `љ`,
  Kazakh `ү`) count against Ukrainian.
- **Shares are compared in whole numbers**, so a boundary such as exactly 20%
  is not tipped by floating-point rounding.
- **The Ukrainian apostrophe `ʼ` (U+02BC) is punctuation here**, even though
  `isalpha()` calls it a letter.
- **Language is never an authorization decision.** `language.py` imports only
  `structure`, and `normalize` copies access straight from the Source Record.
  A test normalizes an English and a Ukrainian record with the same restricted
  access and checks that both keep exactly that access.

**Adversarial review.** An independent review fuzzed `normalize` with about
418,000 records across the four media types and checked `detect_language` on
137,759 exhaustive letter-count combinations, all of Unicode, lone surrogates,
and 20,000 random mixed-script strings. Nothing raised, every outcome was
deterministic, and a known label can never round to confidence 0 (its minimum
is 0.8 x 0.1 = 0.08). It found two breaks in the first version, now fixed,
each with tests that failed against that version:

- `other > (1 - 0.8) * total` compared against `0.19999999999999996`, so text
  with exactly 20% other letters became `unknown` in 19,997 of 20,000 cases.
  After the fix, 0 of 3,997 exact-20% cases are mislabelled.
- Belarusian and Kazakh text, which use the dotted `і`, and neutral Russian,
  Bulgarian, and Macedonian sentences with no marker letters, were labelled
  `uk`. Requiring `ґ`, `є`, or `ї` fixed all of them.

**Known limits of the policy:**

- **Any Latin-script language is labelled `en`.** Polish or German text comes
  out English; the corpus is English and Ukrainian, so this is accepted.
- **Technical Ukrainian flips to `mixed` quickly.** Inline code, file paths,
  and email addresses count as Latin letters; one sentence with
  `src/config/settings.py` and an email address was already `mixed`. Only
  code blocks are skipped, not inline code.
- **Short Ukrainian without `ґ`, `є`, or `ї` is `unknown`.** That is the price
  of not mistaking Belarusian, Kazakh, or Russian for Ukrainian.
- **Old and rare Cyrillic** (Cyrillic Extended-A, B, and C) counts as another
  script. So do Latin letters above `U+024F`, such as Latin Extended
  Additional (used by Vietnamese) and fullwidth Latin; Vietnamese text came
  out `en` with a low confidence of 0.26.

The fixtures hold only synthetic text. No credential or private content
entered any artifact, and the public types name no provider.

## Verification

Final run on 2026-10-19 with `UV_CACHE_DIR=/tmp/uv-cache-rag-project`:

| Command | Result |
|---|---|
| `uv run pytest tests/test_language.py tests/test_normalization.py -q` | 51 passed |
| `make format-check lint typecheck` | 92 files formatted; Ruff: all checks passed; Pyright: 0 errors, 0 warnings |
| `uv run pytest -m "not integration and not live" -q` | 424 passed, 20 deselected |
| `make check` | 425 passed, 17 skipped, 2 deselected; total coverage 85%; `language.py` fully covered |
| `make docs-check` | passed |
| `git diff --check` | clean |

Each prediction held, including the 40-letter Ukrainian sentence at
confidence 0.2 and the Ukrainian paragraph with English terms staying `uk`
at 0.94. Along the way:

- The first lint run failed on `language.py` (a line one character too long
  and no final newline) and on `normalization.py` (the `language` import was
  not in alphabetical order). `make format` and `ruff check --fix` fixed them
  without changing behaviour.
- A typo in the confidence error message (`condidence`) was corrected.

## Reflection

1. **Why the detector reads blocks, not `ParsedDocument.text`.** For HTML,
   `text` is the raw page source, full of English tag and attribute names
   such as `div`, `class`, `content`, and `title`. A Ukrainian page would be
   pushed towards `mixed`, or even `en` for a short page with heavy markup.
   The blocks hold only the visible text, which is what the reader sees and
   what retrieval will search.
2. **Why language must not decide access.** Access answers "who is allowed to
   read this", and only the Source knows that. Language answers "what is this
   written in", which says nothing about permission: a confidential English
   report stays confidential in Kyiv. Even a perfect detector would encode a
   policy nobody wrote down, and a real detector is a guess, so one
   mislabelled `mixed` document would silently become visible to the wrong
   people or vanish for the right ones.
3. **The rule that stopped neutral Russian from being called Ukrainian.**
   Ukrainian now needs positive evidence: at least one `ґ`, `є`, or `ї`, and
   more of them than letters from outside the Ukrainian alphabet. A Russian
   sentence without `ы` or `ё` has neither kind of marker, so it is
   `unknown` instead of defaulting to `uk`. The cost is that a short Ukrainian
   phrase without those three letters, such as "Відпустку погоджують
   заздалегідь.", is `unknown` too.
4. **A test whose removal would let a real bug through.**
   `test_other_cyrillic_languages_are_not_mistaken_for_ukrainian`. Without
   it, a change back to "Cyrillic means Ukrainian unless it has Russian
   letters" would pass every other test, and Belarusian or Kazakh documents
   would be counted in the Ukrainian evaluation slices, making the Ukrainian
   retrieval scores wrong.
5. **What complexity `detect_language` hides from its caller.** The caller
   gets a label and a number. It never counts scripts, knows which letters
   are Ukrainian-only, skips code blocks, worries about the floating-point
   boundary, or decides what "too short" means.
6. **Which alternative would make the next change harder.** Storing the label
   inside access data, or letting later code branch on language for
   permissions, would tie two independent concerns together: changing the
   detector, for example to label inline code differently, would then change
   who can read documents, and every detector change would need a security
   review.

The reflection was drafted by Claude at the learner's request and has not yet
been rewritten in the learner's own words.
