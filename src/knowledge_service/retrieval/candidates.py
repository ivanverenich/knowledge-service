"""The shape every retrieval branch returns its Candidates in.

A Candidate is one Chunk the reader may see, found by one branch, with
everything needed to cite it: which Chunk, which Document and Source, which
version of the text, and the text itself. Vector search returns
`VectorCandidate`, lexical search `LexicalCandidate`; both have these
fields, so code that combines branches, such as fusion, reads them the same
way. Each branch adds its own measure of fit, a distance or a score, which
means nothing to the other branch; only `rank` compares across branches.

What every branch promises about its Candidates:

- **Eligible.** The permission check runs in the same SQL statement as the
  ranking, so a Chunk the reader may not see is never ranked, never counted,
  and never leaves the database.
- **Ranked.** `rank` counts from 1 in result order, best first. Equal fits
  are ordered by Chunk ID, so the same data and query give the same order.
- **Bounded.** At most `limit` Candidates, from 1 to `MAX_CANDIDATES`, and
  `truncated` says whether more eligible Chunks exist.
- **Quiet.** The fields holding Document text are kept out of repr, so
  logging a Candidate or a result never shows them.
"""

from typing import Protocol

from knowledge_service.documents import ContentVersion
from knowledge_service.identifiers import ChunkId, DocumentId, SourceId


class Candidate(Protocol):
    """One eligible Chunk found by one branch, and where it came from."""

    @property
    def rank(self) -> int: ...

    @property
    def chunk_id(self) -> ChunkId: ...

    @property
    def document_id(self) -> DocumentId: ...

    @property
    def source_id(self) -> SourceId: ...

    @property
    def content_version(self) -> ContentVersion: ...

    @property
    def ordinal(self) -> int: ...

    @property
    def external_id(self) -> str: ...

    @property
    def title(self) -> str: ...

    @property
    def heading_path(self) -> str | None: ...

    @property
    def source_anchor(self) -> str | None: ...

    @property
    def text(self) -> str: ...
