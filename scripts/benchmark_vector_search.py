"""Run the vector benchmark and write its report.

Point `KNOWLEDGE_SERVICE_DATABASE_URL` at an empty, migrated database; the
benchmark refuses any other. Every `BenchmarkParameters` field is an
option, such as `--documents 2000` or `--private-share 0.9`. The report goes
to `artifacts/benchmarks/vector-query-plans.md`, which Git ignores, and the
results table is printed.
"""

import argparse
import asyncio
import dataclasses
import sys
from pathlib import Path

from knowledge_service.database import DatabaseRuntime, create_database_runtime
from knowledge_service.settings import Settings
from knowledge_service.vector_benchmark import (
    BenchmarkParameters,
    BenchmarkReport,
    InvalidBenchmark,
    VectorBenchmarkError,
    render_report,
    run_benchmark,
)

REPORT = Path("artifacts/benchmarks/vector-query-plans.md")
# Building an index over a large corpus takes longer than a request may.
STATEMENT_TIMEOUT_MS = 600_000


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    defaults = BenchmarkParameters()
    for field in dataclasses.fields(BenchmarkParameters):
        default: int | float = getattr(defaults, field.name)
        parser.add_argument(
            f"--{field.name.replace('_', '-')}", type=type(default), default=default
        )
    parser.add_argument("--output", type=Path, default=REPORT)
    arguments = parser.parse_args()
    try:
        parameters = BenchmarkParameters(
            **{
                field.name: getattr(arguments, field.name)
                for field in dataclasses.fields(BenchmarkParameters)
            }
        )
    except InvalidBenchmark as error:
        sys.stderr.write(f"invalid parameters: {error}\n")
        return 2
    runtime = create_database_runtime(
        Settings(database_statement_timeout_ms=STATEMENT_TIMEOUT_MS)
    )
    try:
        report = asyncio.run(_run(runtime, parameters))
    except VectorBenchmarkError as error:
        sys.stderr.write(f"benchmark failed: {error}\n")
        return 1
    rendered = render_report(report)
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(rendered, encoding="utf-8")
    summary = rendered.split("## Plan:", 1)[0].rstrip()
    sys.stdout.write(f"{summary}\n\nFull report: {arguments.output}\n")
    return 0


async def _run(
    runtime: DatabaseRuntime, parameters: BenchmarkParameters
) -> BenchmarkReport:
    try:
        return await run_benchmark(runtime, parameters)
    finally:
        await runtime.dispose()


if __name__ == "__main__":
    sys.exit(main())
