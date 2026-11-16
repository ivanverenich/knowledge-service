"""Follow-up resolution on real PostgreSQL: stored History, retrieval both ways."""

import logging
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import UTC, datetime

import pytest
from pydantic import SecretStr

from fakes import FakeEmbeddingModel
from knowledge_service.answering.follow_up_evaluation import (
    compare_resolution,
    render_resolution,
)
from knowledge_service.database import DatabaseRuntime, create_database_runtime
from knowledge_service.embeddings.port import EmbeddingLimits
from knowledge_service.retrieval.evaluation_dataset import (
    CORPUS_DIRECTORY,
    DATASET_DIRECTORY,
    REWRITE_RETRIEVAL_FILE,
    CaseFailed,
    DatasetRunRefused,
    RewriteDataset,
    load_rewrite_dataset,
)
from knowledge_service.retrieval.query_rewriting import (
    RewriteOutcome,
    RewriterUnavailable,
)
from knowledge_service.retrieval.rewrite_cases import (
    REWRITE_CASES_FILE,
    load_rewrite_cases,
)
from knowledge_service.retrieval.rewrite_classes import RewriteClass
from knowledge_service.retrieval.rewrite_evaluation import Rollout, score_rewriting
from knowledge_service.settings import Settings
from rewriting_fakes import FakeRewriteModel

pytestmark = pytest.mark.integration

OBSERVED_AT = datetime(2026, 11, 16, 9, 0, tzinfo=UTC)
CORPUS = DATASET_DIRECTORY / CORPUS_DIRECTORY
RESTRICTED_MARKER = "CONFIDENTIAL-SEED-7731"
LIMITS = EmbeddingLimits(max_inputs=64, max_input_tokens=1_000, max_batch_tokens=20_000)


@pytest.fixture
async def runtime(migrated_database: str) -> AsyncIterator[DatabaseRuntime]:
    runtime = create_database_runtime(
        Settings(database_url=SecretStr(migrated_database))
    )
    try:
        yield runtime
    finally:
        await runtime.dispose()


@pytest.fixture
def rewrite() -> RewriteDataset:
    return load_rewrite_dataset(
        DATASET_DIRECTORY / REWRITE_RETRIEVAL_FILE,
        load_rewrite_cases(REWRITE_CASES_FILE),
    )


async def test_only_follow_ups_are_resolved_through_their_stored_history(
    runtime: DatabaseRuntime,
    rewrite: RewriteDataset,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    rewriter = FakeRewriteModel()

    comparison = await compare_resolution(
        runtime,
        rewrite,
        CORPUS,
        observed_at=OBSERVED_AT,
        model=FakeEmbeddingModel(limits=LIMITS),
        rewriter=rewriter,
        price=None,
    )

    follow_ups = [
        label
        for label in rewrite.labelled
        if label.rewrite_class is RewriteClass.FOLLOW_UP
    ]
    assert follow_ups
    # Each call holds a follow-up and its earlier Questions, read back from
    # the database through History; never another class, never a Chunk.
    assert [(q, h) for q, _, h in rewriter.calls] == [
        (label.question, label.history) for label in follow_ups
    ]
    assert all(RESTRICTED_MARKER not in repr(call) for call in rewriter.calls)
    for case in comparison.cases:
        if case.rewrite_class is not RewriteClass.FOLLOW_UP:
            assert case.outcome is not RewriteOutcome.REWRITTEN
            assert case.after == case.before
    resolved = [c for c in comparison.cases if c.outcome is RewriteOutcome.REWRITTEN]
    assert {c.case_id for c in resolved} == {label.case_id for label in follow_ups}
    rendered = render_resolution(comparison)
    assert "Only follow_up was switched on" in rendered
    for text in (rendered, caplog.text):
        assert RESTRICTED_MARKER not in text
        assert not any(label.question in text for label in rewrite.labelled)


async def test_resolution_is_measured_case_by_case_and_guards_the_rollout(
    runtime: DatabaseRuntime, rewrite: RewriteDataset
) -> None:
    # The fake rewriter only joins the last earlier Question to the follow-up.
    comparison = await compare_resolution(
        runtime,
        rewrite,
        CORPUS,
        observed_at=OBSERVED_AT,
        model=FakeEmbeddingModel(limits=LIMITS),
        rewriter=FakeRewriteModel(),
        price=None,
    )

    cases = {case.case_id: case for case in comparison.cases}
    # "And abroad?" alone finds nothing; with its earlier Question it does.
    assert not cases["en-follow-up-abroad"].before.found
    assert cases["en-follow-up-abroad"].after.found
    gains = {g.rewrite_class: g for g in score_rewriting(comparison)}
    follow_up = gains[RewriteClass.FOLLOW_UP]
    assert (follow_up.cases, follow_up.rewritten) == (3, 3)
    # Joining texts moves one Ukrainian case down, so rollout is refused.
    assert follow_up.harmed == ("uk-follow-up-abroad",)
    assert follow_up.rollout is Rollout.HARMS


async def test_a_failing_rewriter_leaves_every_case_as_typed(
    runtime: DatabaseRuntime, rewrite: RewriteDataset
) -> None:
    comparison = await compare_resolution(
        runtime,
        rewrite,
        CORPUS,
        observed_at=OBSERVED_AT,
        model=FakeEmbeddingModel(limits=LIMITS),
        rewriter=FakeRewriteModel(error=RewriterUnavailable()),
        price=None,
    )

    assert all(case.after == case.before for case in comparison.cases)
    assert {
        case.fallback
        for case in comparison.cases
        if case.rewrite_class is RewriteClass.FOLLOW_UP
    } == {"RewriterUnavailable"}
    assert comparison.usages == ()


async def test_a_rewriter_bug_stops_the_run_without_quoting_the_question(
    runtime: DatabaseRuntime, rewrite: RewriteDataset
) -> None:
    question = next(
        label.question
        for label in rewrite.labelled
        if label.rewrite_class is RewriteClass.FOLLOW_UP
    )

    with pytest.raises(CaseFailed, match="resolving raised RuntimeError") as caught:
        await compare_resolution(
            runtime,
            rewrite,
            CORPUS,
            observed_at=OBSERVED_AT,
            model=FakeEmbeddingModel(limits=LIMITS),
            rewriter=FakeRewriteModel(error=RuntimeError(question)),
            price=None,
        )

    assert question not in str(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


# --- Review regressions ---------------------------------------------------------------


async def test_a_dataset_not_from_its_loader_is_refused(
    runtime: DatabaseRuntime, rewrite: RewriteDataset
) -> None:
    shuffled = replace(
        rewrite,
        dataset=replace(rewrite.dataset, cases=rewrite.dataset.cases[::-1]),
    )

    with pytest.raises(DatasetRunRefused):
        await compare_resolution(
            runtime,
            shuffled,
            CORPUS,
            observed_at=OBSERVED_AT,
            model=FakeEmbeddingModel(limits=LIMITS),
            rewriter=FakeRewriteModel(),
            price=None,
        )


async def test_a_case_named_like_a_resolved_one_keeps_its_own_outcome(
    runtime: DatabaseRuntime, rewrite: RewriteDataset
) -> None:
    # A legal case ID that ends the way a resolved copy's ID could.
    taken = "en-follow-up-abroad-resolved"
    victim = "en-en-annual-leave-days"
    renamed = RewriteDataset(
        dataset=replace(
            rewrite.dataset,
            cases=tuple(
                replace(c, case_id=taken) if c.case_id == victim else c
                for c in rewrite.dataset.cases
            ),
        ),
        labelled=tuple(
            replace(c, case_id=taken) if c.case_id == victim else c
            for c in rewrite.labelled
        ),
    )

    comparison = await compare_resolution(
        runtime,
        renamed,
        CORPUS,
        observed_at=OBSERVED_AT,
        model=FakeEmbeddingModel(limits=LIMITS),
        rewriter=FakeRewriteModel(),
        price=None,
    )

    cases = {case.case_id: case for case in comparison.cases}
    # Searched as typed, the annual leave Question finds nothing in this run.
    assert not cases[taken].before.found
    assert cases[taken].after == cases[taken].before
