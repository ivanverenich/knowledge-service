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

`--sparse` is the M05-T10 prototype. It asks the same local server for
BGE-M3's sparse term weights, runs every case with PostgreSQL word search
and with sparse search, and writes quality, cost, and latency to
`artifacts/evaluations/bilingual-seed-sparse.md`.

`--rerank` runs every case with dense and lexical search fused, using the
local BGE-M3 server, then reranks each case's Candidates with the local
reranker server (`LOCAL_RERANKER_URL`, default `http://127.0.0.1:8767`),
which must already run the pinned revision; start it with
`uv run --project ml/bge_m3_server python ml/bge_m3_server/reranker_server.py`.
It writes the ranking delta and the latency on the reranker's device to
`artifacts/evaluations/bilingual-seed-rerank.md`.

`--llm-rerank` runs the same fused search, then asks an OpenAI model
(`LLM_RERANK_MODEL`, default `gpt-5.4-mini-2026-03-17`) to order each
case's top Candidates, using `KNOWLEDGE_SERVICE_MODEL_API_KEY`. Only the
question and the text of Candidates the reader may see are sent, under
random IDs. It writes the ranking delta, the fallbacks, the timing, and
the billed tokens with their estimated cost to
`artifacts/evaluations/bilingual-seed-llm-rerank.md`.

`--compare-rerankers` runs the same fused search once per case and gives
its Candidates to both rerankers above, so it needs the BGE-M3 server, the
reranker server, and `KNOWLEDGE_SERVICE_MODEL_API_KEY`. It writes no
reranker, the cross-encoder, and the language model side by side, with
quality, moves, fallbacks, latency, and cost, to
`artifacts/evaluations/bilingual-seed-reranking-strategies.md`.
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
    compare_llm_reranking,
    compare_reranking,
    compare_reranking_strategies,
    compare_sparse,
    load_dataset,
    render_comparison,
    render_llm_rerank_comparison,
    render_report,
    render_rerank_comparison,
    render_sparse_comparison,
    render_strategy_comparison,
    run_dataset,
    verify_corpus,
)
from knowledge_service.retrieval.llm_reranking import RankingBudget
from knowledge_service.retrieval.local_reranker import (
    LocalReranker,
    LocalRerankerSettings,
)
from knowledge_service.retrieval.openai_ranking import (
    OpenAIRankingModel,
    OpenAIRankingSettings,
)
from knowledge_service.retrieval.reranking import RerankerError
from knowledge_service.settings import Settings

REPORT = Path("artifacts/evaluations/bilingual-seed.md")
MANAGED_REPORT = Path("artifacts/evaluations/bilingual-seed-managed.md")
LOCAL_REPORT = Path("artifacts/evaluations/bilingual-seed-local.md")
SPARSE_REPORT = Path("artifacts/evaluations/bilingual-seed-sparse.md")
RERANK_REPORT = Path("artifacts/evaluations/bilingual-seed-rerank.md")
LLM_RERANK_REPORT = Path("artifacts/evaluations/bilingual-seed-llm-rerank.md")
STRATEGIES_REPORT = Path("artifacts/evaluations/bilingual-seed-reranking-strategies.md")
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
    embeddings.add_argument("--sparse", action="store_true")
    embeddings.add_argument("--rerank", action="store_true")
    embeddings.add_argument("--llm-rerank", action="store_true")
    embeddings.add_argument("--compare-rerankers", action="store_true")
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
        ranks = arguments.llm_rerank or arguments.compare_rerankers
        if ranks and api_key is None:
            sys.stderr.write("set KNOWLEDGE_SERVICE_MODEL_API_KEY to rank with\n")
            return 1
        source: ModelSource | None = None
        default = REPORT
        if arguments.managed_embeddings and api_key is not None:
            source, default = _openai_model(api_key), MANAGED_REPORT
        if arguments.local_embeddings:
            source, default = _local_model, LOCAL_REPORT
        runtime = create_database_runtime(settings)
        if arguments.sparse:
            default = SPARSE_REPORT
            rendered = asyncio.run(
                _run_sparse(runtime, dataset, corpus, arguments.limit)
            )
        elif arguments.rerank:
            default = RERANK_REPORT
            rendered = asyncio.run(
                _run_rerank(runtime, dataset, corpus, arguments.limit)
            )
        elif arguments.llm_rerank and api_key is not None:
            default = LLM_RERANK_REPORT
            rendered = asyncio.run(
                _run_llm_rerank(runtime, dataset, corpus, arguments.limit, api_key)
            )
        elif arguments.compare_rerankers and api_key is not None:
            default = STRATEGIES_REPORT
            rendered = asyncio.run(
                _run_strategies(runtime, dataset, corpus, arguments.limit, api_key)
            )
        else:
            rendered = asyncio.run(
                _run(runtime, dataset, corpus, arguments.limit, source)
            )
    except DatasetError as error:
        sys.stderr.write(f"dataset run failed: {error}\n")
        return 1
    except EmbeddingError as error:
        sys.stderr.write(f"the embedding model is not ready: {error!r}\n")
        return 1
    except RerankerError as error:
        sys.stderr.write(f"the reranker is not ready: {error!r}\n")
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


async def _run_sparse(
    runtime: DatabaseRuntime, dataset: EvaluationDataset, corpus: Path, limit: int
) -> str:
    try:
        async with _local_model() as model:
            comparison = await compare_sparse(
                runtime,
                dataset,
                corpus,
                observed_at=datetime.now(UTC),
                model=model,
                limit=limit,
            )
        return render_sparse_comparison(comparison)
    finally:
        await runtime.dispose()


async def _run_rerank(
    runtime: DatabaseRuntime, dataset: EvaluationDataset, corpus: Path, limit: int
) -> str:
    try:
        async with (
            _local_model() as model,
            httpx.AsyncClient(trust_env=False) as client,
        ):
            reranker = LocalReranker(client, LocalRerankerSettings())
            # Before anything is written, so a stopped server leaves it empty.
            device = await reranker.verify_server(timeout_seconds=SERVER_CHECK_SECONDS)
            comparison = await compare_reranking(
                runtime,
                dataset,
                corpus,
                observed_at=datetime.now(UTC),
                model=model,
                reranker=reranker,
                device=device,
                limit=limit,
            )
        return render_rerank_comparison(comparison)
    finally:
        await runtime.dispose()


async def _run_llm_rerank(
    runtime: DatabaseRuntime,
    dataset: EvaluationDataset,
    corpus: Path,
    limit: int,
    api_key: SecretStr,
) -> str:
    settings = OpenAIRankingSettings()
    budget = RankingBudget(
        max_candidates=settings.max_candidates,
        max_input_tokens=settings.max_input_tokens,
    )
    client = AsyncOpenAI(api_key=api_key.get_secret_value())
    try:
        async with _local_model() as model:
            ranker = OpenAIRankingModel(client, settings)
            comparison = await compare_llm_reranking(
                runtime,
                dataset,
                corpus,
                observed_at=datetime.now(UTC),
                model=model,
                ranker=ranker,
                budget=budget,
                price=ranker.price,
                limit=limit,
            )
        return render_llm_rerank_comparison(comparison)
    finally:
        await client.close()
        await runtime.dispose()


async def _run_strategies(
    runtime: DatabaseRuntime,
    dataset: EvaluationDataset,
    corpus: Path,
    limit: int,
    api_key: SecretStr,
) -> str:
    try:
        settings = OpenAIRankingSettings()
        budget = RankingBudget(
            max_candidates=settings.max_candidates,
            max_input_tokens=settings.max_input_tokens,
        )
        async with (
            _local_model() as model,
            httpx.AsyncClient(trust_env=False) as client,
            AsyncOpenAI(api_key=api_key.get_secret_value()) as openai,
        ):
            reranker = LocalReranker(client, LocalRerankerSettings())
            # Before anything is written, so a stopped server leaves it empty.
            device = await reranker.verify_server(timeout_seconds=SERVER_CHECK_SECONDS)
            ranker = OpenAIRankingModel(openai, settings)
            comparison = await compare_reranking_strategies(
                runtime,
                dataset,
                corpus,
                observed_at=datetime.now(UTC),
                model=model,
                reranker=reranker,
                device=device,
                ranker=ranker,
                budget=budget,
                price=ranker.price,
                limit=limit,
            )
        return render_strategy_comparison(comparison)
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
async def _local_model() -> AsyncGenerator[LocalEmbeddingModel]:
    # trust_env=False: a proxy set in the environment must never see the texts.
    async with httpx.AsyncClient(trust_env=False) as client:
        model = LocalEmbeddingModel(client, LocalEmbeddingSettings())
        # Before anything is written, so a stopped server leaves the database empty.
        await model.verify_server(timeout_seconds=SERVER_CHECK_SECONDS)
        yield model


if __name__ == "__main__":
    sys.exit(main())
