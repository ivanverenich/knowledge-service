"""Telemetry keeps raw Questions, Evidence, and Answers out unless asked.

Each test seeds sentences that must never be exported, sends records
through the handler `configure_telemetry` installs, and scans every JSON
line it writes.
"""

import io
import json
import logging
from collections.abc import Generator
from contextlib import contextmanager
from typing import Any

import pytest
from pydantic import SecretStr, ValidationError

from knowledge_service.answering.evidence import build_evidence
from knowledge_service.answering.streaming import FoundEvidence
from knowledge_service.contracts import Question
from knowledge_service.settings import Environment, Settings
from knowledge_service.telemetry import (
    REDACTED,
    DiagnosticsNotAllowed,
    TelemetryHandler,
    configure_telemetry,
    diagnostics_enabled,
    remove_telemetry,
)
from test_generation import candidate
from test_streaming import SIGNALS, Model, Source, chunks, reply_json, streamer

QUESTION = Question(text="Is the zebra merger with Northwind going ahead in May?")
EVIDENCE_TEXT = "The zebra merger closes on 12 May for 40 million."
ANSWER = "Yes, the zebra merger closes on 12 May."
SEEDED = "zebra"


@contextmanager
def exported(*, diagnostics: bool = False) -> Generator[io.StringIO]:
    """Install telemetry writing to a buffer; restore logging afterwards."""
    root = logging.getLogger()
    level = root.level
    buffer = io.StringIO()
    handler = configure_telemetry(
        Settings(environment=Environment.TEST, telemetry_diagnostics=diagnostics),
        stream=buffer,
    )
    try:
        yield buffer
    finally:
        remove_telemetry(handler)
        root.setLevel(level)


def lines(buffer: io.StringIO) -> list[dict[str, Any]]:
    return [json.loads(line) for line in buffer.getvalue().splitlines()]


async def ask() -> None:
    evidence = build_evidence([candidate(1, text=EVIDENCE_TEXT)])
    cited = evidence.items[0].evidence_id.value
    model = Model(final=reply_json(ANSWER, citations=[cited]))
    source = Source(FoundEvidence(evidence, SIGNALS))
    await chunks(streamer(model, source=source), QUESTION)


async def test_an_answer_exports_its_counts_and_none_of_its_text() -> None:
    with exported() as buffer:
        await ask()

    records = lines(buffer)
    ended = next(r for r in records if r["message"] == "answer stream ended")
    assert ended["answer_stream"]["characters"] == len(ANSWER)
    assert ended["answer_stream"]["outcome"] == "completed"
    # Every field the stream logs is allowlisted, so nothing was dropped.
    assert not [r for r in records if "redacted_fields" in r]
    assert SEEDED not in buffer.getvalue()


async def test_development_diagnostics_export_the_raw_text() -> None:
    with exported(diagnostics=True) as buffer:
        assert diagnostics_enabled()
        await ask()

    records = lines(buffer)
    assert (
        records[0]["message"] == "development diagnostics enabled: raw text is logged"
    )
    ended = next(r for r in records if r["message"] == "answer stream ended")
    assert ended["diagnostic"] == {
        "question": QUESTION.text,
        "evidence": [EVIDENCE_TEXT],
        "answer": ANSWER,
    }
    assert not diagnostics_enabled()


async def test_with_diagnostics_off_no_record_holds_the_raw_text(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    with exported():
        await ask()

    # pytest's own handler sees records before any filter: still nothing.
    assert caplog.records
    for record in caplog.records:
        assert not hasattr(record, "diagnostic")
        assert SEEDED not in repr(record.__dict__)


OWN = logging.getLogger("knowledge_service.anything")


def test_text_in_fields_and_arguments_is_dropped_or_redacted() -> None:
    with exported() as buffer:
        OWN.info(
            "asked %s after %d tries",
            "the zebra question",
            3,
            extra={
                "answer": {
                    "outcome": "zebra merger",
                    "request_id": "ok-1",
                    "question": "zebra",
                    "citations": ["zebra"],
                },
                "unknown": "zebra",
                "diagnostic": {"question": "zebra"},
            },
        )
        OWN.info("words %s %s %s", "злиття", "z" * 129, "y" * 128)
        OWN.info("rate %s", float("nan"))
        OWN.info("payload %s", {"the zebra merger": 1})
        OWN.info(Question(text="zebra"))  # type: ignore[arg-type]

    asked, words, rate, payload, shown = lines(buffer)
    assert asked["message"] == f"asked {REDACTED} after 3 tries"
    # A list in an allowlisted field is redacted; unknown keys are dropped.
    assert asked["answer"] == {
        "outcome": REDACTED,
        "request_id": "ok-1",
        "citations": REDACTED,
    }
    assert "diagnostic" not in asked
    assert asked["redacted_fields"] == 3
    assert words["message"] == f"words {REDACTED} {REDACTED} {'y' * 128}"
    assert rate["message"] == f"rate {REDACTED}"
    assert payload["message"] == "payload %s"
    assert shown["message"] == REDACTED
    assert SEEDED not in buffer.getvalue()


def test_exceptions_and_stacks_export_only_the_type() -> None:
    with exported() as buffer:
        try:
            raise ValueError("the zebra merger")
        except ValueError:
            OWN.exception("check failed")
        OWN.error("not raised", exc_info=ValueError("the zebra merger"))
        OWN.info("where", stack_info=True)

    raised, given, where = lines(buffer)
    assert raised["exception"] == given["exception"] == "ValueError"
    for line in (raised, given, where):
        assert "traceback" not in line
        assert "stack" not in line
    assert SEEDED not in buffer.getvalue()


def test_another_librarys_message_is_redacted() -> None:
    with exported() as buffer:
        logging.getLogger("asyncio").error(
            "Task exception was never retrieved\nexception=ValueError('zebra')"
        )
        logging.getLogger("httpx").info("[%s] %r", "generated", ("zebra question",))

    for line in lines(buffer):
        assert line["message"] == REDACTED
    assert SEEDED not in buffer.getvalue()


def test_other_handlers_still_see_the_original_record(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    with exported() as buffer:
        OWN.info("asked %s", "the zebra question")

    assert REDACTED in buffer.getvalue()
    assert caplog.records[-1].getMessage() == "asked the zebra question"


def test_a_record_the_filter_cannot_clean_is_dropped_not_raised() -> None:
    with exported() as buffer:
        (handler,) = [
            h for h in logging.getLogger().handlers if isinstance(h, TelemetryHandler)
        ]
        # Straight to the handler: pytest's own would fail on this record.
        handler.handle(
            logging.makeLogRecord(
                {
                    "name": OWN.name,
                    "levelno": logging.WARNING,
                    "msg": "zebra",
                    "exc_info": 5,
                }
            )
        )

    assert [line["message"] for line in lines(buffer)] == ["telemetry record dropped"]


def test_configuring_again_replaces_the_handler_and_its_diagnostics() -> None:
    with exported(diagnostics=True), exported() as buffer:
        assert not diagnostics_enabled()
        OWN.info("once")

    assert [line["message"] for line in lines(buffer)] == ["once"]
    handlers = logging.getLogger().handlers
    assert not [h for h in handlers if isinstance(h, TelemetryHandler)]


def production(*, diagnostics: bool) -> Settings:
    return Settings(
        environment=Environment.PRODUCTION,
        model_api_key=SecretStr("test-key"),
        database_url=SecretStr("postgresql+asyncpg://u@h/db"),
        telemetry_diagnostics=diagnostics,
    )


def test_production_refuses_development_diagnostics() -> None:
    settings = production(diagnostics=False)
    assert not settings.telemetry_diagnostics

    with pytest.raises(ValidationError, match="TELEMETRY_DIAGNOSTICS"):
        production(diagnostics=True)
    # Settings changed after validation are refused when telemetry starts.
    changed = settings.model_copy(update={"telemetry_diagnostics": True})
    with pytest.raises(DiagnosticsNotAllowed):
        configure_telemetry(changed, stream=io.StringIO())
    assert not diagnostics_enabled()
