"""The model list of the OpenAI-compatible adapter: one virtual knowledge model.

An OpenAI client, such as Open WebUI, asks `GET {base}/models` which models
it may choose. This service offers exactly one: a virtual model that answers
from the knowledge base through the native Question workflow, whatever
language model runs behind it. Its ID and display name come from
configuration, and its creation time and owner are fixed, so a client's
saved choice keeps working across restarts and upgrades, and nothing names
the provider or the real model behind it.

A refusal has OpenAI's error shape, `{"error": {...}}`, with the native
API's fixed codes and messages, so an OpenAI client reads it as an error
and never as a model list.
"""

import re
import unicodedata
from dataclasses import dataclass
from typing import Final, Literal

from fastapi.responses import JSONResponse
from pydantic import BaseModel

# Midnight UTC on 2026-11-25, when the virtual model was first served. It
# never changes: a model whose creation time moves looks like a new model.
MODEL_CREATED: Final = 1_795_564_800
MODEL_OWNER: Final = "knowledge-service"
DEFAULT_MODEL_ID: Final = "knowledge-base"
DEFAULT_MODEL_NAME: Final = "Knowledge base"
MAX_MODEL_NAME: Final = 64
# Lowercase letters, digits, and `.`, `_`, `:`, `-`: an ID a client can put
# in a URL or a setting without escaping.
MODEL_ID_PATTERN: Final = r"[a-z0-9][a-z0-9._:-]{0,63}"
_MODEL_ID = re.compile(MODEL_ID_PATTERN)


class InvalidCompatibleModel(ValueError):
    """A configured model ID or name breaks its rule."""


@dataclass(frozen=True, slots=True)
class CompatibleModel:
    """The one virtual model the adapter offers, as configured."""

    model_id: str = DEFAULT_MODEL_ID
    name: str = DEFAULT_MODEL_NAME

    def __post_init__(self) -> None:
        if type(self.model_id) is not str or not _MODEL_ID.fullmatch(self.model_id):
            raise InvalidCompatibleModel(
                "the model ID must be 1 to 64 lowercase letters, digits,"
                " `.`, `_`, `:`, or `-`, starting with a letter or digit"
            )
        if (
            type(self.name) is not str
            or not self.name
            or len(self.name) > MAX_MODEL_NAME
            or self.name != self.name.strip()
            or not self.name.isprintable()
            # One spelling of each letter, so the length counts what shows.
            or unicodedata.normalize("NFC", self.name) != self.name
            or not any(_shows(character) for character in self.name)
        ):
            raise InvalidCompatibleModel(
                f"the model name must be visible text of 1 to {MAX_MODEL_NAME}"
                " characters in NFC, with no space around it"
            )


def _shows(character: str) -> bool:
    """Whether a character is seen on its own: not a mark, space, or control."""
    return unicodedata.category(character)[0] not in "MZC"


class ModelObject(BaseModel):
    """One model, as OpenAI's `GET /models` lists it, with Open WebUI's `name`."""

    id: str
    object: Literal["model"] = "model"
    created: int
    owned_by: str
    name: str


class ModelList(BaseModel):
    """OpenAI's list of models: all of them, in one page."""

    object: Literal["list"] = "list"
    data: list[ModelObject]


def model_list(model: CompatibleModel) -> ModelList:
    """The list a permitted caller sees: the one configured model."""
    return ModelList(
        data=[
            ModelObject(
                id=model.model_id,
                created=MODEL_CREATED,
                owned_by=MODEL_OWNER,
                name=model.name,
            )
        ]
    )


class CompatibleErrorDetail(BaseModel):
    """What went wrong, in OpenAI's words: a fixed message, a type, a code."""

    message: str
    type: str
    param: str | None = None
    code: str


class CompatibleError(BaseModel):
    """OpenAI's error body: one `error` object, never a list or a model."""

    error: CompatibleErrorDetail


# OpenAI's error type for each status this adapter answers with.
_ERROR_TYPES: Final = {
    400: "invalid_request_error",
    401: "authentication_error",
    403: "permission_error",
    404: "invalid_request_error",
    405: "invalid_request_error",
    500: "server_error",
    503: "server_error",
}


def compatible_error(
    *, status_code: int, code: str, message: str, headers: dict[str, str] | None = None
) -> JSONResponse:
    """An error response in OpenAI's shape, with the native code and message."""
    body = CompatibleError(
        error=CompatibleErrorDetail(
            message=message,
            type=_ERROR_TYPES.get(status_code, "server_error"),
            code=code,
        )
    )
    return JSONResponse(
        status_code=status_code,
        content=body.model_dump(mode="json"),
        headers=headers,
    )
