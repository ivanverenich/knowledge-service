---
status: accepted
---

# Normalize source access and enforce it inside retrieval

Every Source maps its access rules into shared Access Grants, and each vector or lexical query filters Candidates using the verified Authorization Context. Filtering after retrieval is insufficient because unauthorized text could already reach ranking, telemetry, prompts, or timing-visible behavior.
