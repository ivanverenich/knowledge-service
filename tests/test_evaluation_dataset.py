"""The bilingual seed dataset: its file rules, its corpus check, its report."""

import json
import os
import shutil
import subprocess
import sys
from collections import Counter
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from fractions import Fraction
from pathlib import Path
from typing import Any

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from pydantic import SecretStr

from fakes import FakeEmbeddingModel
from knowledge_service.database import create_database_runtime
from knowledge_service.embeddings.port import EmbeddingModel, EmbeddingModelIdentity
from knowledge_service.retrieval import evaluation_dataset
from knowledge_service.retrieval.evaluation_dataset import (
    CASES_FILE,
    CORPUS_DIRECTORY,
    DATASET_DIRECTORY,
    DIRECTIONS,
    MAX_FILE_BYTES,
    CaseOutcome,
    DatasetReport,
    DatasetRunRefused,
    InvalidDataset,
    QueryClass,
    compare_branches,
    load_dataset,
    render_comparison,
    render_report,
    run_dataset,
    score_slices,
    verify_corpus,
)
from knowledge_service.retrieval.fusion import RetrievalBranch
from knowledge_service.retrieval.hybrid import BranchRequest, BranchResult, BranchSearch
from knowledge_service.retrieval.lexical_search import MAX_QUERY_CHARACTERS
from knowledge_service.retrieval.vector_search import MAX_CANDIDATES
from knowledge_service.settings import Settings

MARKER = "PLANTED-MARKER-4417"

type Content = dict[str, Any]
type Edit = Callable[[Content], None]


def seed() -> Content:
    return json.loads((DATASET_DIRECTORY / CASES_FILE).read_text(encoding="utf-8"))


def copy_dataset(tmp_path: Path) -> Path:
    target = tmp_path / "dataset"
    shutil.copytree(DATASET_DIRECTORY, target)
    return target


def write_cases(folder: Path, content: Content) -> Path:
    path = folder / CASES_FILE
    path.write_text(json.dumps(content, ensure_ascii=False), encoding="utf-8")
    return path


def edited(edit: Edit) -> Content:
    content = seed()
    edit(content)
    return content


def first_case(content: Content) -> Content:
    return content["cases"][0]


def top(key: str, value: object) -> Edit:
    def edit(content: Content) -> None:
        content[key] = value

    return edit


def case_field(key: str, value: object) -> Edit:
    def edit(content: Content) -> None:
        first_case(content)[key] = value

    return edit


def without_case_field(key: str) -> Edit:
    def edit(content: Content) -> None:
        del first_case(content)[key]

    return edit


def reference(key: str, value: str) -> Edit:
    def edit(content: Content) -> None:
        first_case(content)["relevant"][0][key] = value

    return edit


def duplicate_first_case(content: Content) -> None:
    content["cases"].append(dict(first_case(content)))


def relevant_from_restricted(content: Content) -> None:
    case = first_case(content)
    case["relevant"].append(case["ineligible"][0])


def ineligible_from_public(content: Content) -> None:
    case = first_case(content)
    case["ineligible"].append(case["relevant"][0])


def relevant_twice(content: Content) -> None:
    case = first_case(content)
    case["relevant"].append(case["relevant"][0])


def drop_class(query_class: str) -> Edit:
    def edit(content: Content) -> None:
        content["cases"] = [
            case for case in content["cases"] if case["query_class"] != query_class
        ]

    return edit


def only_english_evidence(content: Content) -> None:
    content["cases"] = [
        case for case in content["cases"] if case["evidence_language"] == "en"
    ]


def without_ineligible(content: Content) -> None:
    for case in content["cases"]:
        case.pop("ineligible", None)


async def test_the_seed_dataset_loads_and_matches_its_corpus() -> None:
    dataset = load_dataset(DATASET_DIRECTORY / CASES_FILE)

    await verify_corpus(dataset, DATASET_DIRECTORY / CORPUS_DIRECTORY)

    assert (dataset.name, dataset.version) == ("bilingual-seed", 1)
    directions = Counter(case.direction for case in dataset.cases)
    classes = Counter(case.query_class for case in dataset.cases)
    assert all(directions[direction] >= 2 for direction in DIRECTIONS)
    assert classes[QueryClass.IDENTIFIER] >= 2
    assert classes[QueryClass.MORPHOLOGY] >= 2
    assert any(case.ineligible for case in dataset.cases)


def test_a_case_keeps_its_question_out_of_repr() -> None:
    case = load_dataset(DATASET_DIRECTORY / CASES_FILE).cases[0]

    assert case.question not in repr(case)


@pytest.mark.parametrize(
    ("edit", "message"),
    [
        (case_field("extra", 1), "<unknown>: Extra inputs"),
        (without_case_field("question"), "question"),
        (case_field("question_language", "de"), "question_language"),
        (case_field("query_class", "fuzzy"), "query_class"),
        (case_field("relevant", []), "relevant"),
        (top("version", True), "version"),
        (top("version", 0), "version"),
        (top("cases", []), "cases"),
        (top("name", "Bilingual Seed"), "name"),
        (case_field("id", "Case 1"), "id"),
        (duplicate_first_case, "more than once"),
        (case_field("question", "   "), "letter or digit"),
        (case_field("question", "-" * 40), "letter or digit"),
        (case_field("question", chr(0x200B)), "letter or digit"),
        (case_field("question", "a" * (MAX_QUERY_CHARACTERS + 1)), "characters"),
        (case_field("question", "leave\x00days"), "valid text"),
        (reference("document", "annual-leave.md"), "public/<path>"),
        (reference("document", "private/a.md"), "public/<path>"),
        (reference("document", "public/Annual-Leave.md"), "public/<path>"),
        (reference("document", "public/../restricted/a.md"), "public/<path>"),
        (reference("document", "public/" + "a" * 200 + ".md"), "public/<path>"),
        (reference("heading", " "), "heading"),
        (relevant_from_restricted, "relevant evidence must be in public/"),
        (ineligible_from_public, "ineligible evidence must be in restricted/"),
        (relevant_twice, "same Chunk twice"),
        (drop_class("morphology"), "slice morphology"),
        (drop_class("identifier"), "slice identifier"),
        (only_english_evidence, "slice en->uk"),
        (without_ineligible, "ineligible evidence"),
    ],
)
def test_a_cases_file_that_breaks_a_rule_is_refused(
    tmp_path: Path, edit: Edit, message: str
) -> None:
    path = write_cases(tmp_path, edited(edit))

    with pytest.raises(InvalidDataset, match=message):
        load_dataset(path)


@pytest.mark.parametrize(
    ("content", "message"),
    [
        (b"", "file"),
        (b"{not json", "file"),
        (b"[]", "file"),
        (b'{"name": "x", "version": 1, "cases": [', "file"),
        (b" " * (MAX_FILE_BYTES + 1), "larger than"),
    ],
)
def test_a_file_that_is_not_a_dataset_is_refused(
    tmp_path: Path, content: bytes, message: str
) -> None:
    path = tmp_path / CASES_FILE
    path.write_bytes(content)

    with pytest.raises(InvalidDataset, match=message):
        load_dataset(path)


def test_a_missing_cases_file_is_refused(tmp_path: Path) -> None:
    with pytest.raises(InvalidDataset, match="not a regular file"):
        load_dataset(tmp_path / CASES_FILE)


def test_an_unreadable_cases_file_is_refused(tmp_path: Path) -> None:
    path = write_cases(tmp_path, seed())
    path.chmod(0)
    try:
        with pytest.raises(InvalidDataset, match="cannot be read: PermissionError"):
            load_dataset(path)
    finally:
        path.chmod(0o600)


def test_a_pipe_in_place_of_the_cases_file_is_refused_without_waiting(
    tmp_path: Path,
) -> None:
    os.mkfifo(tmp_path / CASES_FILE)

    with pytest.raises(InvalidDataset, match="not a regular file"):
        load_dataset(tmp_path / CASES_FILE)


@pytest.mark.parametrize(
    "content",
    [
        b'{"name": "a", "name": "b", "version": 1, "cases": []}',
        b'{"name": "bilingual-seed", "version": NaN, "cases": []}',
        b"[" * 100_000,
        b'{"version": 1' + b"0" * 5000 + b"}",
        b"\xff\xfe",
    ],
)
def test_json_that_is_ambiguous_or_broken_is_refused(
    tmp_path: Path, content: bytes
) -> None:
    path = tmp_path / CASES_FILE
    path.write_bytes(content)

    with pytest.raises(InvalidDataset, match="not valid UTF-8 JSON"):
        load_dataset(path)


def test_a_key_written_twice_is_refused_in_the_real_file(tmp_path: Path) -> None:
    raw = (DATASET_DIRECTORY / CASES_FILE).read_text(encoding="utf-8")
    twice = raw.replace('"question": ', f'"question": "{MARKER}", "question": ', 1)
    path = tmp_path / CASES_FILE
    path.write_text(twice, encoding="utf-8")

    with pytest.raises(InvalidDataset) as raised:
        load_dataset(path)
    assert MARKER not in str(raised.value)


@pytest.mark.parametrize(
    "edit",
    [
        case_field(MARKER + "\nforged: line", 1),
        reference("document", f"public/{MARKER}\x1b[2J\nforged: line"),
    ],
)
def test_an_error_never_repeats_a_key_or_a_path_from_the_file(
    tmp_path: Path, edit: Edit
) -> None:
    path = write_cases(tmp_path, edited(edit))

    with pytest.raises(InvalidDataset) as raised:
        load_dataset(path)
    assert MARKER not in str(raised.value)
    assert "\n" not in str(raised.value)


json_values = st.recursive(
    st.none()
    | st.booleans()
    | st.integers()
    | st.floats(allow_nan=False)
    | st.text(max_size=20).map(lambda text: MARKER + text),
    lambda children: (
        st.lists(children, max_size=3)
        | st.dictionaries(st.text(max_size=8), children, max_size=3)
    ),
    max_leaves=10,
)
fields = st.sampled_from(
    [
        "id",
        "question",
        "question_language",
        "evidence_language",
        "query_class",
        "relevant",
        "ineligible",
    ]
)


@settings(
    max_examples=200,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(name=fields, value=json_values)
def test_any_field_value_raises_only_invalid_dataset_without_echoing_it(
    tmp_path: Path, name: str, value: object
) -> None:
    content = seed()
    first_case(content)[name] = value
    path = write_cases(tmp_path, content)

    # Some values are valid, such as a new question; any error must be typed.
    message = ""
    try:
        load_dataset(path)
    except InvalidDataset as error:
        message = str(error)
    assert MARKER not in message


@settings(
    max_examples=200,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(value=json_values)
def test_any_reference_value_raises_only_invalid_dataset_without_echoing_it(
    tmp_path: Path, value: object
) -> None:
    content = seed()
    first_case(content)["relevant"] = [value]
    path = write_cases(tmp_path, content)

    with pytest.raises(InvalidDataset) as raised:
        load_dataset(path)
    assert MARKER not in str(raised.value)


@pytest.mark.parametrize(
    ("edit", "message"),
    [
        (reference("document", "public/missing.md"), "not in the corpus"),
        (reference("heading", "Annual leave"), "0 Chunks"),
        (case_field("evidence_language", "uk"), "detected as en, not uk"),
    ],
)
async def test_a_case_that_does_not_match_the_corpus_is_refused(
    tmp_path: Path, edit: Edit, message: str
) -> None:
    folder = copy_dataset(tmp_path)
    dataset = load_dataset(write_cases(folder, edited(edit)))

    with pytest.raises(InvalidDataset, match=message):
        await verify_corpus(dataset, folder / CORPUS_DIRECTORY)


async def test_a_heading_shared_by_two_chunks_is_refused(tmp_path: Path) -> None:
    folder = copy_dataset(tmp_path)
    document = folder / CORPUS_DIRECTORY / "public" / "annual-leave.md"
    text = document.read_text(encoding="utf-8")
    section = text[text.index("## Days per year") :].split("\n\n## ")[0]
    document.write_text(f"{text}\n{section}\n", encoding="utf-8")
    dataset = load_dataset(folder / CASES_FILE)

    with pytest.raises(InvalidDataset, match="2 Chunks"):
        await verify_corpus(dataset, folder / CORPUS_DIRECTORY)


async def test_a_corpus_document_that_cannot_be_ingested_is_refused(
    tmp_path: Path,
) -> None:
    folder = copy_dataset(tmp_path)
    (folder / CORPUS_DIRECTORY / "restricted" / "empty.md").write_text("")
    dataset = load_dataset(folder / CASES_FILE)

    with pytest.raises(InvalidDataset, match=r"restricted/empty.md cannot be"):
        await verify_corpus(dataset, folder / CORPUS_DIRECTORY)


async def test_a_missing_corpus_folder_is_refused(tmp_path: Path) -> None:
    folder = copy_dataset(tmp_path)
    shutil.rmtree(folder / CORPUS_DIRECTORY / "restricted")
    dataset = load_dataset(folder / CASES_FILE)

    with pytest.raises(InvalidDataset, match="restricted/ cannot be read"):
        await verify_corpus(dataset, folder / CORPUS_DIRECTORY)


async def test_the_corpus_is_read_page_by_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(evaluation_dataset, "PAGE_SIZE", 1)
    dataset = load_dataset(DATASET_DIRECTORY / CASES_FILE)

    await verify_corpus(dataset, DATASET_DIRECTORY / CORPUS_DIRECTORY)


async def never_called(request: BranchRequest) -> BranchResult:
    raise AssertionError("no branch may run")


@pytest.mark.parametrize(
    ("limit", "branches", "message"),
    [
        (0, None, "limit"),
        (MAX_CANDIDATES + 1, None, "limit"),
        (True, None, "limit"),
        (10, {}, "at least one branch"),
        (10, {"lexical": never_called}, "every branch"),
        (10, {RetrievalBranch.LEXICAL: "search"}, "every branch"),
    ],
)
async def test_a_wrong_setting_is_refused_before_any_work(
    limit: int, branches: Mapping[RetrievalBranch, BranchSearch] | None, message: str
) -> None:
    # The runtime connects lazily, so nothing is reached at this address.
    runtime = create_database_runtime(
        Settings(database_url=SecretStr("postgresql+psycopg://u:p@localhost:1/x"))
    )
    dataset = load_dataset(DATASET_DIRECTORY / CASES_FILE)

    with pytest.raises(DatasetRunRefused, match=message):
        await run_dataset(
            runtime,
            dataset,
            DATASET_DIRECTORY / CORPUS_DIRECTORY,
            observed_at=datetime(2026, 11, 3, tzinfo=UTC),
            limit=limit,
            branches=branches,
        )
    await runtime.dispose()


def test_the_report_counts_slices_and_shows_ranks_only() -> None:
    report = DatasetReport(
        name="bilingual-seed",
        version=1,
        limit=10,
        branches=(RetrievalBranch.LEXICAL,),
        outcomes=(
            CaseOutcome("a", "en->en", QueryClass.PLAIN, (1,), degraded=False),
            CaseOutcome("b", "en->en", QueryClass.IDENTIFIER, (None,), degraded=False),
            CaseOutcome("c", "uk->en", QueryClass.PLAIN, (None, 3), degraded=True),
        ),
    )

    rendered = render_report(report)

    assert "| en->en | 2 | 1 |" in rendered
    assert "| uk->en | 1 | 1 |" in rendered
    assert "| uk->uk | 0 | 0 |" in rendered
    assert "| plain | 2 | 2 |" in rendered
    assert "| c | uk->en | plain | missed, 3 | yes |" in rendered
    assert rendered.startswith("# Retrieval dataset report: bilingual-seed v1\n")


SCORED = DatasetReport(
    name="bilingual-seed",
    version=1,
    limit=5,
    branches=(RetrievalBranch.DENSE, RetrievalBranch.LEXICAL),
    outcomes=(
        CaseOutcome("a", "en->en", QueryClass.PLAIN, (1,), degraded=False),
        CaseOutcome("b", "en->en", QueryClass.IDENTIFIER, (4,), degraded=False),
        CaseOutcome("c", "en->en", QueryClass.PLAIN, (None,), degraded=True),
        CaseOutcome("d", "uk->en", QueryClass.PLAIN, (None, 3), degraded=False),
    ),
    embedding=EmbeddingModelIdentity(
        provider="openai", model="text-embedding-3-small", dimensions=1536
    ),
)


def test_slices_get_exact_recall_and_mrr() -> None:
    scores = {score.name: score for score in score_slices(SCORED)}

    en_en = scores["en->en"]
    assert (en_en.cases, en_en.found, en_en.degraded) == (3, 2, 1)
    # Cut-offs above the run's limit of 5 are left out.
    assert en_en.recall == (
        (1, Fraction(1, 3)),
        (3, Fraction(1, 3)),
        (5, Fraction(2, 3)),
    )
    assert en_en.mrr == Fraction(5, 12)
    # Two relevant Chunks, one found at rank 3: half of them, best rank 3.
    assert scores["uk->en"].recall[-1] == (5, Fraction(1, 2))
    assert scores["uk->en"].mrr == Fraction(1, 3)
    assert scores["all"].cases == 4
    assert scores["uk->uk"].recall == ()
    assert scores["uk->uk"].mrr is None


def test_the_report_shows_the_configuration_and_the_scores() -> None:
    rendered = render_report(SCORED)

    assert "- Branches: dense, lexical." in rendered
    assert "- Embedding model: openai text-embedding-3-small, 1536 dimensions." in (
        rendered
    )
    assert "- Fusion: Reciprocal Rank Fusion, k = 60." in rendered
    assert "| Slice | Cases | Found | Recall@1 | Recall@3 | Recall@5 | MRR |" in (
        rendered
    )
    assert "| en->en | 3 | 2 | 0.333 | 0.333 | 0.667 | 0.417 | 1 |" in rendered
    assert "| uk->uk | 0 | 0 | n/a | n/a | n/a | n/a | 0 |" in rendered


def test_the_comparison_puts_every_run_side_by_side() -> None:
    lexical = DatasetReport(
        name="bilingual-seed",
        version=1,
        limit=5,
        branches=(RetrievalBranch.LEXICAL,),
        outcomes=tuple(
            CaseOutcome(
                o.case_id,
                o.direction,
                o.query_class,
                (None,) * len(o.ranks),
                degraded=False,
            )
            for o in SCORED.outcomes
        ),
    )

    rendered = render_comparison((lexical, SCORED))

    assert rendered.startswith("# Retrieval comparison: bilingual-seed v1\n")
    assert "- Runs: lexical; dense + lexical." in rendered
    assert "- Embedding model: openai text-embedding-3-small" in rendered
    assert (
        "| Slice | Cases | lexical R@5 | lexical MRR"
        " | dense + lexical R@5 | dense + lexical MRR |"
    ) in rendered
    assert "| en->en | 3 | 0.000 | 0.000 | 0.667 | 0.417 |" in rendered
    assert rendered.count("# Retrieval dataset report:") == 2


class NamelessModel(FakeEmbeddingModel):
    @property
    def identity(self) -> EmbeddingModelIdentity:
        raise RuntimeError("no identity")


@pytest.mark.parametrize(
    ("limit", "model", "message"),
    [
        (0, FakeEmbeddingModel(), "limit"),
        (True, FakeEmbeddingModel(), "limit"),
        (10, NamelessModel(), "EmbeddingModelIdentity"),
    ],
)
async def test_a_wrong_comparison_setting_is_refused_before_any_work(
    limit: int, model: EmbeddingModel, message: str
) -> None:
    runtime = create_database_runtime(
        Settings(database_url=SecretStr("postgresql+psycopg://u:p@localhost:1/x"))
    )
    dataset = load_dataset(DATASET_DIRECTORY / CASES_FILE)

    with pytest.raises(DatasetRunRefused, match=message):
        await compare_branches(
            runtime,
            dataset,
            DATASET_DIRECTORY / CORPUS_DIRECTORY,
            observed_at=datetime(2026, 11, 4, tzinfo=UTC),
            model=model,
            limit=limit,
        )
    await runtime.dispose()


def test_verify_only_needs_no_settings() -> None:
    # Only a run reads the settings, so a bad one cannot stop the check.
    finished = subprocess.run(
        [sys.executable, "scripts/run_retrieval_dataset.py", "--verify-only"],
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "KNOWLEDGE_SERVICE_LOG_LEVEL": "not-a-level"},
        timeout=60,
    )

    assert finished.returncode == 0, finished.stderr
    assert finished.stdout == "16 cases verified\n"
