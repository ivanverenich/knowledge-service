---
status: accepted
---

# Group code into packages by module

By the end of M04 the `knowledge_service` package held 45 modules in one
folder, and `persistence.py` alone held every table and every statement in
1,875 lines. Finding the code behind one module of this architecture meant
reading file names and imports. The code now lives in one package per
module, and persistence is split by the kind of record it stores.

| Package | Holds |
|---|---|
| `knowledge_service.ingestion` | Sources, normalization, chunking, provenance, reconciliation, publishing, synchronization, deletions |
| `knowledge_service.ingestion.parsers` | The format parsers and the structural blocks they produce |
| `knowledge_service.embeddings` | The EmbeddingModel port and its OpenAI adapter, batching, publication, index configurations, model migrations |
| `knowledge_service.retrieval` | Vector search, retrieval filters, the vector benchmark |
| `knowledge_service.persistence` | `schema` (every table, the one `metadata` Alembic compares) and one module per kind of record |

Domain values (`identifiers`, `access`, `documents`, `chunks`, `language`,
`audit`, `jobs`, `conversations`, `evaluation`), configuration, the database
runtime, and the HTTP application stay at the top level, where every module
may import them.

## Consequences

- Imports name the module that owns the code, such as
  `knowledge_service.persistence.chunks`; no package re-exports another
  module's names, so an import shows where the code lives.
- Behaviour did not change: the move kept every test, schema, and statement;
  only imports, a few docstrings and comments, and one logger name changed. The
  OpenAI embedding adapter now logs as
  `knowledge_service.embeddings.openai_adapter`.
- Lesson guides and evidence written before this decision name the old flat
  paths. They record the code as it was at their commit and are not
  rewritten.
- A new module goes into the package of the architecture module it serves;
  a new package needs a reason recorded here.
