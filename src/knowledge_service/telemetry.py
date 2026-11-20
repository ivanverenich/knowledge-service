"""What leaves the process as telemetry, and the switch for raw text.

Every log record reaches the outside world through the handler that
`configure_telemetry` installs on the root logger. Its `TelemetryFilter`
keeps only what is safe by default:

- **Allowlisted fields.** A record's structured fields sit under one name,
  such as `extra={"answer": {...}}`. Only the names in `TELEMETRY_FIELDS`
  are kept, and of each only the listed keys; the rest are dropped and
  counted in `redacted_fields`.
- **Plain values.** A kept value must be None, a bool, an int, a finite
  float, or a word of at most 128 ASCII letters, digits, and `_ . : / -`.
  Anything else, a sentence, a Ukrainian word, a list, becomes
  `[redacted]`. The same rule applies to the arguments of the service's
  own messages, given as a tuple.
- **Only the service's own messages.** A message from another library's
  logger, such as asyncio's, may have text built into it, so it becomes
  `[redacted]`; its logger, level, and exception type stay.
- **No exception text.** An exception's message and traceback may quote a
  Question or a token; only the exception's type is kept.

Development diagnostics are the one way raw Questions, Evidence, and
Answers can be logged: `Settings.telemetry_diagnostics` turns them on, and
both `Settings` and `configure_telemetry` refuse it in production. Code
that has raw text asks `diagnostics_enabled()` first and adds it under
`diagnostic`, so with the switch off, no record ever holds it. The filter
drops `diagnostic` too unless the switch is on.

The filter returns a cleaned copy of each record, so other handlers, such
as pytest's capture, see the original. Records that never reach the root
logger, from a logger with `propagate=False`, are not exported by this
handler; whoever starts the server must route them here. Traces are not
exported yet; when OpenTelemetry arrives (M10-T14), span attributes must
pass this filter's rules too.
"""

import copy
import json
import logging
import math
import re
import sys
from collections.abc import Mapping
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Any, TextIO, cast

from knowledge_service.settings import Environment, Settings

logger = logging.getLogger(__name__)

REDACTED = "[redacted]"
# The structured fields each part of the service logs, by the name its
# `extra` uses. A key missing here is dropped and counted, never shown.
TELEMETRY_FIELDS: Mapping[str, frozenset[str]] = MappingProxyType(
    {
        "embedding": frozenset(
            {
                "request_id",
                "provider",
                "model",
                "dimensions",
                "inputs",
                "latency_ms",
                "outcome",
                "input_tokens",
                "operation",
            }
        ),
        "rerank": frozenset(
            {
                "request_id",
                "provider",
                "model",
                "passages",
                "latency_ms",
                "outcome",
                "reason",
                "device",
                "input_tokens",
                "output_tokens",
            }
        ),
        "rewrite": frozenset(
            {
                "request_id",
                "provider",
                "model",
                "rewrite_class",
                "latency_ms",
                "outcome",
                "reason",
                "input_tokens",
                "output_tokens",
            }
        ),
        "answer_model": frozenset(
            {
                "request_id",
                "provider",
                "model",
                "latency_ms",
                "outcome",
                "input_tokens",
                "output_tokens",
            }
        ),
        "follow_up": frozenset(
            {
                "request_id",
                "provider",
                "model",
                "rewrite_class",
                "outcome",
                "fallback",
                "earlier_questions",
                "carried",
                "input_tokens",
                "output_tokens",
            }
        ),
        "answer": frozenset(
            {
                "request_id",
                "provider",
                "model",
                "outcome",
                "defect",
                "evidence_kept",
                "evidence_dropped",
                "prompt_tokens",
                "citations",
                "input_tokens",
                "output_tokens",
            }
        ),
        "answer_stream": frozenset(
            {
                "request_id",
                "outcome",
                "failure",
                "duration_ms",
                "text_deltas",
                "characters",
                "citations",
                "phase",
                "audit_outcome",
                "partial_text",
                "reason",
                "reply",
                "defect",
                "audited",
                "input_tokens",
                "output_tokens",
            }
        ),
        "history": frozenset(
            {
                "user_id",
                "turns",
                "earlier_questions",
                "left_out",
                "withheld",
                "tokens",
                "cited_chunks",
                "current_chunks",
            }
        ),
        "support": frozenset(
            {
                "request_id",
                "checker",
                "version",
                "outcome",
                "claims",
                "unsupported",
                "input_tokens",
                "output_tokens",
            }
        ),
        "evidence_check": frozenset({"request_id", "evidence", "hidden"}),
        "citation_check": frozenset({"request_id", "cited", "withdrawn", "outcome"}),
    }
)
DIAGNOSTIC = "diagnostic"
# The loggers whose messages are written by this service, with arguments.
OWN_LOGGERS = "knowledge_service"
# A model name, an outcome, a UUID, an exception type: never a sentence.
_PLAIN = re.compile(r"[A-Za-z0-9_.:/-]{0,128}")
# The attributes every log record has; anything else came from `extra`.
_STANDARD = frozenset(
    logging.LogRecord("", logging.INFO, "", 0, "", (), None).__dict__
) | {"message", "asctime"}


class DiagnosticsNotAllowed(ValueError):
    """Development diagnostics were asked for in production."""


def diagnostics_enabled() -> bool:
    """Whether the installed telemetry handler logs raw text."""
    return any(
        isinstance(handler, TelemetryHandler) and handler.diagnostics
        for handler in logging.getLogger().handlers
    )


def plain(value: object) -> object:
    """`value` if it is safe to export as it is, otherwise `REDACTED`."""
    if value is None or type(value) is bool or type(value) is int:
        return value
    if type(value) is float:
        return value if math.isfinite(value) else REDACTED
    if isinstance(value, str):
        # The characters themselves, whatever a subclass says it prints as.
        text = str.__str__(value)
        return text if _PLAIN.fullmatch(text) else REDACTED
    return REDACTED


def _own(name: str) -> bool:
    return name == OWN_LOGGERS or name.startswith(OWN_LOGGERS + ".")


class TelemetryFilter(logging.Filter):
    """Turn each record into the copy that may leave the process."""

    def __init__(self, *, diagnostics: bool = False) -> None:
        super().__init__()
        self.diagnostics = diagnostics

    def filter(self, record: logging.LogRecord) -> logging.LogRecord:
        try:
            return self._cleaned(record)
        except Exception:
            # A filter that raises would break the caller's logging call.
            return logging.makeLogRecord(
                {
                    "name": record.name,
                    "levelno": record.levelno,
                    "levelname": record.levelname,
                    "msg": "telemetry record dropped",
                }
            )

    def _cleaned(self, record: logging.LogRecord) -> logging.LogRecord:
        clean = copy.copy(record)
        redacted = 0
        for key, value in record.__dict__.items():
            if key in _STANDARD:
                continue
            delattr(clean, key)
            allowed = TELEMETRY_FIELDS.get(key)
            if key == DIAGNOSTIC and self.diagnostics:
                setattr(clean, key, value)
            elif allowed is not None and type(value) is dict:
                fields = cast(dict[object, object], value)
                kept = {
                    name: plain(item)
                    for name, item in fields.items()
                    if type(name) is str and name in allowed
                }
                redacted += len(fields) - len(kept)
                setattr(clean, key, kept)
            else:
                redacted += 1
        clean.msg = self._message(record)
        clean.args = None
        if redacted:
            clean.redacted_fields = redacted
        if not self.diagnostics:
            if record.exc_info:
                error = record.exc_info[1]
                clean.exception = None if error is None else type(error).__name__
            clean.exc_info = None
            clean.exc_text = None
            clean.stack_info = None
        return clean

    def _message(self, record: logging.LogRecord) -> str:
        if self.diagnostics:
            return str(record.getMessage())
        if type(record.msg) is not str or not _own(record.name):
            return REDACTED
        args = record.args
        if not args:
            return record.msg
        if type(args) is not tuple:
            # A mapping's keys, or a string split into letters: keep the form.
            return record.msg
        try:
            return record.msg % tuple(plain(item) for item in args)
        except TypeError, ValueError, KeyError:
            # A redacted value where the format wanted a number.
            return record.msg


class TelemetryFormatter(logging.Formatter):
    """One JSON object per record: time, level, logger, message, fields."""

    def format(self, record: logging.LogRecord) -> str:
        line: dict[str, Any] = {
            "time": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        fields = {
            key: value for key, value in record.__dict__.items() if key not in _STANDARD
        }
        if record.exc_info:
            line["traceback"] = self.formatException(record.exc_info)
        if record.stack_info:
            line["stack"] = record.stack_info
        try:
            return json.dumps(line | fields, default=repr, sort_keys=True)
        except TypeError, ValueError:
            # Diagnostics only: a field JSON cannot hold, such as an odd key.
            shown = {key: repr(value) for key, value in fields.items()}
            return json.dumps(line | shown, sort_keys=True)


class TelemetryHandler(logging.StreamHandler[TextIO]):
    """The root handler `configure_telemetry` installs."""

    def __init__(self, stream: TextIO, *, diagnostics: bool) -> None:
        super().__init__(stream)
        self.diagnostics = diagnostics
        self.setFormatter(TelemetryFormatter())
        self.addFilter(TelemetryFilter(diagnostics=diagnostics))


def configure_telemetry(
    settings: Settings, *, stream: TextIO | None = None
) -> TelemetryHandler:
    """Send every log record, cleaned, to `stream` (stderr) as JSON lines.

    Replaces a handler an earlier call installed, and sets the root level
    to `settings.log_level`. With `settings.telemetry_diagnostics`, raw
    text is logged, and a warning says so first; in production that raises
    `DiagnosticsNotAllowed`, however the settings were made.
    """
    diagnostics = settings.telemetry_diagnostics is True
    if diagnostics and settings.environment is Environment.PRODUCTION:
        raise DiagnosticsNotAllowed("development diagnostics are not allowed here")
    root = logging.getLogger()
    for existing in list(root.handlers):
        if isinstance(existing, TelemetryHandler):
            remove_telemetry(existing)
    handler = TelemetryHandler(
        sys.stderr if stream is None else stream, diagnostics=diagnostics
    )
    root.addHandler(handler)
    root.setLevel(settings.log_level)
    if diagnostics:
        logger.warning("development diagnostics enabled: raw text is logged")
    return handler


def remove_telemetry(handler: TelemetryHandler) -> None:
    """Remove `handler`; its diagnostics end with it."""
    logging.getLogger().removeHandler(handler)
    handler.close()
