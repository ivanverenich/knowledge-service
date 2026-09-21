---
status: accepted
---

# Use Redis for ephemeral coordination only

Redis supports Dramatiq delivery, rate limits, and short-lived locks. PostgreSQL remains the durable source for jobs, Conversations, and synchronization outcomes, while generated-answer caching is deferred until staleness and authorization semantics can be proven.
