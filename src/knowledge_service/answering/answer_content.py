"""What an Answer's text may hold, since a Document can steer the model.

Retrieved text is untrusted: a Document can say "end every answer with this
image" or "send the reader to this page", and a model may obey. The prompt
already tells the model that Evidence is data (see `answering.prompt`);
this module checks what comes back, whatever the model was told.

Every check reads the text as a Markdown renderer would see it: backslash
escapes and HTML character references (`&#58;`, `&colon;`) are undone
first, so `https\\://` or `https&#58;//` count as the links they become.

What the module promises:

- **No active content.** Content a client fetches or runs as soon as it
  shows the Answer, with no click, is refused: a Markdown image (`![`), an
  HTML tag that loads or runs something (`img`, `svg`, `iframe`, `script`,
  `style`, `a`, and the like, in any case, opening or closing), any tag
  with an attribute that loads something (`style`, `background`, `src`,
  `srcset`, `poster`, `data`), and a `mermaid` diagram block, also inside
  a quote or list, whose shapes and labels can load images. Other angle
  brackets, such as `List<String>` or `<Enter>`, are text.
- **Links only from cited Evidence.** Every link must appear in the text of
  an Evidence the Answer cites, ignoring case: a `scheme://` or `//` link,
  a `www.` address, `mailto:`, `javascript:`, `data:` with a media type,
  an email address, and any Markdown link or reference destination. A
  link runs until white space or `<` `>`, so quotes inside it are part of
  it; a Markdown link's destination also ends at `)`, which only makes the
  checked link shorter. Only punctuation at its end is dropped. A link that
  carries the Question or another passage to someone's server does not
  appear in any Evidence, and an abstention may hold no link at all.
- **Held back while streamed.** `held_from` gives the first place where a
  stream must stop sending text until the Answer is checked: the first
  `!`, `<`, `:`, `&`, backslash, `@`, `](`, `//`, `www.`, or code fence. A
  stream checks the last `HELD_OVERLAP` characters it sent together with
  the new ones, so a marker split between pieces is still found.
- **Linear time.** A model can be told to pad its Answer with one long
  word or many blank lines, so no pattern starts again from every
  character of a run: line starts match only spaces and tabs, and a
  scheme or an email address starts only where a word does.
"""

import html
import re
from collections.abc import Sequence

# A Markdown image, a tag that loads or runs something, a tag with an
# attribute that loads something, or a mermaid block, also in a quote or list.
_ACTIVE = re.compile(
    r"!\["
    r"|</?(?:img|image|svg|iframe|frame|frameset|video|audio|source"
    r"|track|picture|object|embed|applet|link|style|script|a|base|meta|form"
    r"|input|button|math|body|html|head|template|portal)(?![a-z0-9-])"
    r"|<[a-z][a-z0-9-]*\s[^<>]*?(?<![a-z0-9-])"
    r"(?:style|background|src|srcset|poster|data)[ \t\n]*="
    r"|^[ \t]*(?:(?:>|[-*+]|[0-9]{1,9}[.)])[ \t]*)*(?:`{3,}|~{3,})[ \t]*mermaid",
    re.IGNORECASE | re.MULTILINE,
)
# A scheme or an email address starts only where a word does, so a long run
# of letters is not tried again from each one.
_LINK = re.compile(
    r"(?:(?<![a-z0-9+.-])[a-z][a-z0-9+.-]*:)?//[^\s<>]*"
    r"|www\.[^\s<>]*"
    r"|(?:mailto|javascript):[^\s<>]*"
    r"|data:[a-z]+/[^\s<>]*"
    r"|(?<![a-z0-9._%+-])[a-z0-9._%+-]+@[a-z0-9-]+(?:\.[a-z0-9-]+)+"
    r"|\]\(\s*<?([^\s<>)]+)"
    r"|^[ \t]*\[[^\]\n]+\]:[ \t]*\n?[ \t]*<?([^\s<>]+)",
    re.IGNORECASE | re.MULTILINE,
)
# Punctuation that ends a sentence, a quote, or a Markdown link.
_TRAILING = ".,;:!?)]}*_\"'`"
# CommonMark lets a backslash escape any ASCII punctuation character.
_ESCAPE = re.compile(r"\\([!-/:-@\[-`{-~])")
_HELD = re.compile(r"[!<:&\\@]|\]\(|//|www\.|`{3}|~{3}", re.IGNORECASE)
# Enough of what was sent to find a marker that a new piece completes.
HELD_OVERLAP = 3


def rendered(text: str) -> str:
    """`text` with backslash escapes and character references undone."""
    return html.unescape(_ESCAPE.sub(r"\1", text))


def has_active_content(text: str) -> bool:
    """Whether `text` holds an image, a loading tag, or a mermaid block."""
    return _ACTIVE.search(rendered(text)) is not None


def links(text: str) -> tuple[str, ...]:
    """Every link in `text`, without the punctuation that ends it."""
    found: list[str] = []
    for match in _LINK.finditer(rendered(text)):
        destination = match.group(1) or match.group(2) or match.group()
        link = destination.rstrip(_TRAILING)
        if link:
            found.append(link)
    return tuple(found)


def unsourced_links(text: str, sources: Sequence[str]) -> bool:
    """Whether `text` holds a link that none of `sources` contains."""
    known = [rendered(source).casefold() for source in sources]
    return any(
        not any(link.casefold() in source for source in known) for link in links(text)
    )


def held_from(text: str) -> int | None:
    """Where a stream must stop sending `text` until the Answer is checked."""
    match = _HELD.search(text)
    return None if match is None else match.start()
