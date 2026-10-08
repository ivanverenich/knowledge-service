---
status: accepted
---

# Application workflows own transaction boundaries

Repository functions take an `AsyncConnection` and never commit. An application
workflow opens one transaction with `DatabaseRuntime.transaction()`, calls the
repository functions it needs, and commits or rolls back once, at the end.

## Consequences

- A Document is published when its row, its Chunks, and its Access Grants are
  written in one transaction. A reader sees the previous complete version or the
  new one, never a mixture of the two.
- External calls — model providers, source APIs, embedding services — happen
  outside the transaction. No transaction is held open across a network call,
  and no row is locked before one.
- A workflow that fails publishes nothing, including the writes it had already
  made inside its own transaction.
- A redelivered job that arrives out of order is refused by the domain inside
  the transaction, so the older run cannot overwrite a newer published version.
