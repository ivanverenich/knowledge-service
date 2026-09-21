---
status: accepted
---

# Use incremental at-least-once synchronization

Sources are reconciled through resumable checkpoints and jobs that may be delivered more than once. Idempotent stage behavior and transactional publication are chosen over an exactly-once claim that cannot be guaranteed across source APIs, Redis, PostgreSQL, and model providers.
