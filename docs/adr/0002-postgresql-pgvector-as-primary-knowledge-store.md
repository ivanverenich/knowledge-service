---
status: accepted
---

# Use PostgreSQL and pgvector as the primary knowledge store

PostgreSQL owns normalized Documents, Access Grants, Conversations, audit metadata, lexical indexes, and vectors. At the target scale, one transactional store makes authorization and publication atomic; dedicated vector or lexical stores remain evidence-driven extensions rather than initial operational dependencies.
