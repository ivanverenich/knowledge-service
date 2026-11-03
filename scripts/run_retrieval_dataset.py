"""Verify the bilingual seed dataset and, given a database, run every case.

`--verify-only` checks the cases against the corpus and needs no database.
Otherwise point `KNOWLEDGE_SERVICE_DATABASE_URL` at an empty, migrated
database; the run refuses any other. The report goes to
`artifacts/evaluations/bilingual-seed.md`, which Git ignores, and is printed.
"""

import argparse
import asyncio
import sys
from datetime import UTC, datetime
from pathlib import Path

from knowledge_service.database import DatabaseRuntime, create_database_runtime
from knowledge_service.retrieval.evaluation_dataset import (
    CASES_FILE,
    CORPUS_DIRECTORY,
    DATASET_DIRECTORY,
    DEFAULT_LIMIT,
    DatasetError,
    DatasetReport,
    EvaluationDataset,
    load_dataset,
    render_report,
    run_dataset,
    verify_corpus,
)
from knowledge_service.settings import Settings

REPORT = Path("artifacts/evaluations/bilingual-seed.md")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DATASET_DIRECTORY)
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    parser.add_argument("--output", type=Path, default=REPORT)
    parser.add_argument("--verify-only", action="store_true")
    arguments = parser.parse_args()
    corpus = arguments.dataset / CORPUS_DIRECTORY
    try:
        dataset = load_dataset(arguments.dataset / CASES_FILE)
        if arguments.verify_only:
            asyncio.run(verify_corpus(dataset, corpus))
            sys.stdout.write(f"{len(dataset.cases)} cases verified\n")
            return 0
        runtime = create_database_runtime(Settings())
        report = asyncio.run(_run(runtime, dataset, corpus, arguments.limit))
    except DatasetError as error:
        sys.stderr.write(f"dataset run failed: {error}\n")
        return 1
    rendered = render_report(report)
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(rendered, encoding="utf-8")
    sys.stdout.write(f"{rendered}\nFull report: {arguments.output}\n")
    return 0


async def _run(
    runtime: DatabaseRuntime, dataset: EvaluationDataset, corpus: Path, limit: int
) -> DatasetReport:
    try:
        return await run_dataset(
            runtime, dataset, corpus, observed_at=datetime.now(UTC), limit=limit
        )
    finally:
        await runtime.dispose()


if __name__ == "__main__":
    sys.exit(main())
