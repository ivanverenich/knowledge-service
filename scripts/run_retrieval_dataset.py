"""Verify the bilingual seed dataset and, given a database, run every case.

`--verify-only` checks the cases against the corpus and needs no database.
Otherwise point `KNOWLEDGE_SERVICE_DATABASE_URL` at an empty, migrated
database; the run refuses any other. By default only word search runs and
the report goes to `artifacts/evaluations/bilingual-seed.md`.

`--managed-embeddings` also embeds the corpus with the OpenAI model set by
`EMBEDDING_MODEL` and `EMBEDDING_DIMENSIONS` (default
`text-embedding-3-small`, 1536), using `KNOWLEDGE_SERVICE_MODEL_API_KEY`.
It runs every case three ways, words only, meaning only, and both fused, and
writes `artifacts/evaluations/bilingual-seed-managed.md`. Git ignores both
reports. Every Chunk, the restricted ones included, is sent to OpenAI; the
seed corpus is made up, so nothing private leaves the machine.
"""

import argparse
import asyncio
import sys
from datetime import UTC, datetime
from pathlib import Path

from openai import AsyncOpenAI
from pydantic import SecretStr

from knowledge_service.database import DatabaseRuntime, create_database_runtime
from knowledge_service.embeddings.openai_adapter import (
    OpenAIEmbeddingModel,
    OpenAIEmbeddingSettings,
)
from knowledge_service.retrieval.evaluation_dataset import (
    CASES_FILE,
    CORPUS_DIRECTORY,
    DATASET_DIRECTORY,
    DEFAULT_LIMIT,
    DatasetError,
    EvaluationDataset,
    compare_branches,
    load_dataset,
    render_comparison,
    render_report,
    run_dataset,
    verify_corpus,
)
from knowledge_service.settings import Settings

REPORT = Path("artifacts/evaluations/bilingual-seed.md")
MANAGED_REPORT = Path("artifacts/evaluations/bilingual-seed-managed.md")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DATASET_DIRECTORY)
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--verify-only", action="store_true")
    parser.add_argument("--managed-embeddings", action="store_true")
    arguments = parser.parse_args()
    corpus = arguments.dataset / CORPUS_DIRECTORY
    try:
        dataset = load_dataset(arguments.dataset / CASES_FILE)
        if arguments.verify_only:
            asyncio.run(verify_corpus(dataset, corpus))
            sys.stdout.write(f"{len(dataset.cases)} cases verified\n")
            return 0
        # Read only for a run, so a bad setting cannot stop the check above.
        settings = Settings()
        api_key = settings.model_api_key
        if arguments.managed_embeddings and api_key is None:
            sys.stderr.write(
                "set KNOWLEDGE_SERVICE_MODEL_API_KEY to embed the corpus\n"
            )
            return 1
        runtime = create_database_runtime(settings)
        rendered = asyncio.run(
            _run(
                runtime,
                dataset,
                corpus,
                arguments.limit,
                api_key if arguments.managed_embeddings else None,
            )
        )
    except DatasetError as error:
        sys.stderr.write(f"dataset run failed: {error}\n")
        return 1
    default = MANAGED_REPORT if arguments.managed_embeddings else REPORT
    output: Path = arguments.output or default
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(rendered, encoding="utf-8")
    sys.stdout.write(f"{rendered}\nFull report: {output}\n")
    return 0


async def _run(
    runtime: DatabaseRuntime,
    dataset: EvaluationDataset,
    corpus: Path,
    limit: int,
    api_key: SecretStr | None,
) -> str:
    observed_at = datetime.now(UTC)
    try:
        if api_key is None:
            report = await run_dataset(
                runtime, dataset, corpus, observed_at=observed_at, limit=limit
            )
            return render_report(report)
        client = AsyncOpenAI(api_key=api_key.get_secret_value())
        try:
            model = OpenAIEmbeddingModel(client, OpenAIEmbeddingSettings())
            reports = await compare_branches(
                runtime,
                dataset,
                corpus,
                observed_at=observed_at,
                model=model,
                limit=limit,
            )
        finally:
            await client.close()
        return render_comparison(reports)
    finally:
        await runtime.dispose()


if __name__ == "__main__":
    sys.exit(main())
