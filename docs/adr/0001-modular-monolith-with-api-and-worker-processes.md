---
status: accepted
---

# Use a modular monolith with API and worker processes

The project uses one codebase and domain model while deploying HTTP and background work as separate processes. This preserves transactional and conceptual locality while teaching distributed execution, retries, and backpressure without premature microservice ownership and network contracts.
