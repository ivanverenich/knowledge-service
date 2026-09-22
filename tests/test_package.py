import importlib

import pytest


def test_package_is_importable() -> None:
    module = importlib.import_module("knowledge_service")

    assert module.__name__ == "knowledge_service"


def test_package_import_has_no_output(capsys: pytest.CaptureFixture[str]) -> None:
    importlib.reload(importlib.import_module("knowledge_service"))

    captured = capsys.readouterr()

    assert captured.out == ""
    assert captured.err == ""


def test_unknown_package_module_fails_with_typed_import_error() -> None:
    with pytest.raises(
        ModuleNotFoundError, match=r"knowledge_service\.not_a_real_module"
    ):
        importlib.import_module("knowledge_service.not_a_real_module")
