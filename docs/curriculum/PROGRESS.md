# Curriculum progress

This file records the learner’s actual progress. Task files remain the reusable instructions; this tracker records completed work and evidence.

| Task | Status | Completed | Evidence | Notes |
|---|---|---|---|---|
| [M00-T01 — Record the workspace baseline](milestones/M00-foundations/M00-T01-record-the-workspace-baseline.md) | completed | 2026-09-21 | [Baseline evidence](../evidence/M00-T01-baseline.md) | Environment recorded; no application code or dependencies created. |
| [M00-T02 — Initialize Git and the public license](milestones/M00-foundations/M00-T02-initialize-git-and-license.md) | completed | 2026-09-21 | Initial commit `3c075e2` | Clean `main` branch; Apache-2.0 license and focused `.gitignore` added; documentation-only checkpoint. |
| [M00-T03 — Pin Python 3.14 and create the uv project](milestones/M00-foundations/M00-T03-pin-python-and-create-uv-project.md) | completed | 2026-09-21 | [uv project evidence](../evidence/M00-T03-uv-project.md) | Locked sync and package import succeeded; implementation files are ready for your next commit. |
| [M00-T04 — Choose the initial package layout](milestones/M00-foundations/M00-T04-choose-the-initial-package-layout.md) | completed | 2026-09-22 | [Package layout evidence](../evidence/M00-T04-package-layout.md) | Minimal src package and deterministic import-boundary tests added; no speculative feature modules. |
| [M00-T05 — Configure formatting, linting, and type checking](milestones/M00-foundations/M00-T05-configure-format-lint-and-types.md) | completed | 2026-09-22 | [Format, lint, and type evidence](../evidence/M00-T05-format-lint-and-types.md) | Ruff format and lint policy plus Pyright strict added; planted violations proved the gates fail, and pytest passed on them. Formatter over-reach into 283 planning documents found and excluded. |
| [M00-T06 — Establish the pytest loop](milestones/M00-foundations/M00-T06-establish-the-pytest-loop.md) | completed | 2026-09-22 | [pytest loop evidence](../evidence/M00-T06-pytest-loop.md) | asyncio mode, markers, 30s timeout, and coverage reporting configured; `live` tests are off by default. Red, strict-marker, and timeout probes all failed as intended. |
| [M00-T07 — Add developer command entrypoints](milestones/M00-foundations/M00-T07-add-developer-command-entrypoints.md) | completed | 2026-09-23 | [Developer command evidence](../evidence/M00-T07-developer-commands.md) | Root Makefile exposes thin setup, format, lint, type-check, unit, integration, and aggregate-check commands; normal test entrypoints explicitly exclude `live`. |
| [M00-T08 — Model typed configuration](milestones/M00-foundations/M00-T08-model-typed-configuration.md) | completed | 2026-09-23 | [Typed configuration evidence](../evidence/M00-T08-typed-configuration.md) | Pydantic Settings provides safe local defaults, production-only credential validation, redacted secrets, and deterministic environment-isolated tests. |

## Next unblocked task

[M00-T09 — Install pre-commit guardrails](milestones/M00-foundations/M00-T09-install-pre-commit-guardrails.md)
