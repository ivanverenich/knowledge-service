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

`--local-embeddings` does the same with the local BGE-M3 server instead
(`LOCAL_EMBEDDING_URL`, default `http://127.0.0.1:8765`), which must already
run the pinned revision; start it with
`uv run --project ml/bge_m3_server python ml/bge_m3_server/bge_m3_server.py`.
The report goes to `artifacts/evaluations/bilingual-seed-local.md`, and no
text leaves the machine.
"""

import argparse
import asyncio
import sys
from collections.abc import AsyncGenerator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

import httpx
from openai import AsyncOpenAI
from pydantic import SecretStr

from knowledge_service.database import DatabaseRuntime, create_database_runtime
from knowledge_service.embeddings.local_adapter import (
    LocalEmbeddingModel,
    LocalEmbeddingSettings,
)
from knowledge_service.embeddings.openai_adapter import (
    OpenAIEmbeddingModel,
    OpenAIEmbeddingSettings,
)
from knowledge_service.embeddings.port import EmbeddingError, EmbeddingModel
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
LOCAL_REPORT = Path("artifacts/evaluations/bilingual-seed-local.md")
SERVER_CHECK_SECONDS = 10.0

type ModelSource = Callable[[], AbstractAsyncContextManager[EmbeddingModel]]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DATASET_DIRECTORY)
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--verify-only", action="store_true")
    embeddings = parser.add_mutually_exclusive_group()
    embeddings.add_argument("--managed-embeddings", action="store_true")
    embeddings.add_argument("--local-embeddings", action="store_true")
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
        source: ModelSource | None = None
        default = REPORT
        if arguments.managed_embeddings and api_key is not None:
            source, default = _openai_model(api_key), MANAGED_REPORT
        if arguments.local_embeddings:
            source, default = _local_model, LOCAL_REPORT
        runtime = create_database_runtime(settings)
        rendered = asyncio.run(_run(runtime, dataset, corpus, arguments.limit, source))
    except DatasetError as error:
        sys.stderr.write(f"dataset run failed: {error}\n")
        return 1
    except EmbeddingError as error:
        sys.stderr.write(f"the embedding model is not ready: {error!r}\n")
        return 1
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
    source: ModelSource | None,
) -> str:
    observed_at = datetime.now(UTC)
    try:
        if source is None:
            report = await run_dataset(
                runtime, dataset, corpus, observed_at=observed_at, limit=limit
            )
            return render_report(report)
        async with source() as model:
            reports = await compare_branches(
                runtime,
                dataset,
                corpus,
                observed_at=observed_at,
                model=model,
                limit=limit,
            )
        return render_comparison(reports)
    finally:
        await runtime.dispose()


def _openai_model(api_key: SecretStr) -> ModelSource:
    @asynccontextmanager
    async def source() -> AsyncGenerator[EmbeddingModel]:
        client = AsyncOpenAI(api_key=api_key.get_secret_value())
        try:
            yield OpenAIEmbeddingModel(client, OpenAIEmbeddingSettings())
        finally:
            await client.close()

    return source


@asynccontextmanager
async def _local_model() -> AsyncGenerator[EmbeddingModel]:
    # trust_env=False: a proxy set in the environment must never see the texts.
    async with httpx.AsyncClient(trust_env=False) as client:
        model = LocalEmbeddingModel(client, LocalEmbeddingSettings())
        # Before anything is written, so a stopped server leaves the database empty.
        await model.verify_server(timeout_seconds=SERVER_CHECK_SECONDS)
        yield model


if __name__ == "__main__":
    sys.exit(main())
