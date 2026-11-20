# M07-T13 — Harden against retrieved instructions evidence

## Invariant

Whatever retrieved text tells the model, nothing it writes can reach a
reader as active content or a foreign link: an Answer or reason holding a
Markdown image, a loading HTML tag, or a mermaid block is refused, every
link in an Answer appears in an Evidence it cites, an abstention holds no
link, and the stream sends no text past the first character that could
start one before the check. The model is offered no tool, and a reply
holding a tool call is not an answer.

## Expected failing check

Before the change, `uv run pytest tests/test_retrieved_instructions.py -q`
fails with `ERROR: file or directory not found: tests/test_retrieved_instructions.py`.

## Predictions

Drafted by Claude at the learner's request, before the code changed but after reading the guide, so they are not blind; the learner should rewrite them in their own words.

- What a client receives when an obedient model ends its Answer with an image from another server: the text up to the `!` of `![`, then `checking`, then the fixed abstention; the image address never leaves the server.
- Whether "Use the [portal](https://intranet.example/leave)." is answered when the cited passage holds that link, and when it does not: answered when the cited passage holds it; refused with `unsourced_link`, so the reader gets an abstention, when it does not.
- What happens when the provider's reply holds a tool call and a message: the reply is unreadable (`INVALID` with `UNREADABLE`), the message is never read, and the usage is still billed.
- How many tests fail if `checked_answer` stops checking links in an answered reply: every `unsourced_link` case of the typed-defect test, the uncited-Evidence test, and the obeyed `link` injection through the stream; about 16.

## Design

- **Input side, already true.** The policy is a constant and the Question and Evidence travel as one escaped JSON document (M06-T02); only Evidence the reader may see reaches the prompt (M07-T07, M07-T09); a Citation can name only this request's Evidence (M06-T05). So an injected Document cannot reveal hidden content or invent a Citation. This lesson guards the output side.
- **What an Answer may hold.** The new `answering.answer_content` module reads text as a Markdown renderer does (`rendered` undoes backslash escapes and character references), then `has_active_content` finds a Markdown image, a tag from a fixed list of tags that load or run something, any tag with a loading attribute (`style`, `background`, `src`, `srcset`, `poster`, `data`), and a mermaid fence, also inside a quote or list. `links` finds `scheme://`, `//`, `www.`, `mailto:`, `javascript:`, `data:`, email addresses, and Markdown link and reference destinations; `unsourced_links` says whether one is missing from every source text, ignoring case.
- **Refuse in `checked_answer`.** Two new defects: `ACTIVE_CONTENT` for an answer or reason with active content, and `UNSOURCED_LINK` for an answered reply with a link no cited Evidence contains, or an abstention with any link. The reader gets the usual abstention.
- **Hold the stream.** `AnswerStreamer` stops sending text at the first `!`, `<`, `:`, `&`, backslash, `@`, `](`, `//`, `www.`, or code fence (`held_from`), checking the last `HELD_OVERLAP` (3) sent characters with each new piece; the rest is sent only after an `answer` decision.
- **No tools.** The request offers none; `_reply` in `openai_answering` reads any output item but `message` or `reasoning`, or a non-string type, as unreadable and still billed, on both the plain and the streaming path.
- **Linear time.** Line starts match only spaces and tabs, and a scheme or email starts only where a word does, so a padded 16,000-character Answer is checked in milliseconds.

## Verification

Completed 2026-11-20.

- Expected failing check: before the change, `uv run pytest tests/test_retrieved_instructions.py -q` gave `ERROR: file or directory not found: tests/test_retrieved_instructions.py`.
- Step checks: `uv run pyright src/knowledge_service/answering/answer_content.py` 0 errors; `tests/test_generation.py`, `tests/test_openai_answering.py`, `tests/test_openai_streaming.py`, `tests/test_streaming.py` passed unchanged (151).
- `uv run pytest tests/test_retrieved_instructions.py -q`: 60 passed with the guide's tests; 73 passed after the review fixes added 13 cases.
- Deliberate break (link check removed from answered replies): 16 failed, 44 passed on the guide's tests, and 16 failed, 57 passed after the review fixes; restored: all pass. The prediction (about 16) was right.
- Gates after the review fixes: `make format-check` (292 files formatted), `make lint`, `make typecheck` (0 errors), `make docs-check` clean; `make test-unit` 2868 passed (2855 with the guide's tests, +13 from the review); `make test-integration` 641 passed with PostgreSQL and Keycloak running (unchanged); `git diff --check` clean.
- Telemetry: every stream injection case scans the log text and every record's fields for `evil.example` and `zebra`; none appear.
- Known limits: a link the cited Document itself contains passes; a bare domain such as `evil.example/x`, which some renderers turn into a link, is not checked; other diagram or math renderers a client adds later need the same review; an escaped `!\[` is refused although a renderer shows it as text, which only costs an abstention.

## Review

The guide's own adversarial review (escaped schemes, `//host`, mermaid, false positives on `<Enter>`, a crash on a list-typed output item, 22 mutations) is summarised in the guide's "Before you begin". Two further independent reviews ran on this implementation:

- **Standards (blocking, fixed):** the patterns took quadratic time: `^\s*` under MULTILINE rescanned blank lines from every line start, and the scheme and email parts restarted at every letter. A Document could order padding and block the event loop: 1.9 s on 16,000 `a`, 3.0 s on 16,000 newlines. Now about 3 ms, with `test_a_padded_answer_is_checked_in_linear_time`. The same fix stopped "```\nmermaid is ..." being refused. The docstring now says a Markdown destination ends at `)`.
- **Spec (no blocking findings; non-blocking ones fixed):** a tag outside the list with a `style` or `background` attribute could load a URL from the cited Document; a mermaid fence inside a quote or list item passed; a tool call was tested only on the non-streaming path. Each now has a test. The stream boundary, the reason check, abstentions sending no held text, and both adapter paths were confirmed sound.

## Decision

Drafted by Claude at the learner's request; the learner should rewrite it in their own words.

- **Refuse, do not clean.** Removing an image would change text that was already checked and streamed; refusing keeps one rule: the client gets exactly the checked text or an abstention.
- **A fixed list of tags plus loading attributes.** Refusing every `<` turns ordinary IT answers (`<Enter>`, `List<String>`) into abstentions; refusing none lets `<img>` through. Naming what loads keeps both.
- **Links only from cited Evidence.** The reader could already open that Document, so its links add nothing new; a link with the Question appended, or any other server, appears in no passage.
- **Hold the stream.** The check runs on the whole reply, but text streams before it; without a stop the image would reach the client first.

**Falsified assumption.** I assumed that matching active content and links with regular expressions was cheap enough to ignore. The Standards review showed `^\s*` and unanchored scheme and email parts take seconds on a 16,000-character padded answer. So "a regex check costs nothing next to a model call" is wrong for text an attacker shapes; patterns on model output need a time bound and a test for it.

## Reflection

Drafted by Claude at the learner's request; the learner should rewrite it in their own words.

1. M07-T07 and M07-T09 put only Evidence the reader may see into the prompt, and M06-T05 lets Citations name only this request's Evidence. The model never has another User's Document, so it cannot reveal it, whatever it is told.
2. An image loads as soon as the Answer is shown, with no click, so the Question leaves even if the reader notices nothing; a link needs the reader to choose to click it.
3. Refusing every `<` would turn answers about keys, generics, or comparisons into abstentions; refusing none would let `<img src=...>` load a URL with the Question in it.
4. Not a new hole: the reader can open that Document and see the same link. It still matters which Sources are trusted, so a malicious Document can still mislead, and M07-T15's suite should keep an eye on it.
5. Every tool result must pass through the same output check (`checked_answer`'s active-content and link rules) and be treated as untrusted Evidence. With tools, an injected Document could make the model act (delete, send, fetch) rather than just write, so tool calls need policy outside the model.
