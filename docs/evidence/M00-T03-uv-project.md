# M00-T03 — uv project evidence

Completed: 2026-09-21

## Verified files

- `.python-version` contains `3.14`.
- `pyproject.toml` declares project `knowledge-service`.
- `requires-python` is `>=3.14,<3.15`.
- Runtime `dependencies` are intentionally empty.
- Development tools are declared in the `dev` dependency group.
- `uv.lock` exists and records the resolved environment.
- The package module is `src/knowledge_service/__init__.py`.

## Verification commands

```text
UV_CACHE_DIR=/tmp/uv-cache-rag-project uv sync --locked
UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run python -c 'import knowledge_service; print(knowledge_service.__file__)'
```

Results:

```text
Resolved 14 packages in 3ms
Checked 13 packages in 0.45ms
/Users/ivanverenich/Documents/searching_job/rag_project/src/knowledge_service/__init__.py
```

No application feature code was added. The generated `__init__.py` starter content remains for M00-T04 to replace when the package layout is intentionally chosen.
