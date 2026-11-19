from pathlib import Path
from unittest.mock import MagicMock

import pytest

from codebase_rag import constants as cs
from codebase_rag.config import settings
from codebase_rag.tests.conftest import get_nodes, get_relationships, run_updater

pytest.importorskip("pcpp")

PROJECT = "c_pp"

# A method-definition macro that builds the function name by token pasting,
# the shape tree-sitter cannot name from raw source.
HEADER = (
    "#define ENTRY\n"
    "#define METHOD_DECL(t, m) _M_##t##_##m\n"
    "static inline int header_helper(void) { return 2; }\n"
    "static inline int header_other(void) { return 3; }\n"
)
SOURCE = (
    '#include "compiler.h"\n'
    "static int helper(void) { return 1; }\n"
    "int ENTRY PlainEntry(void) { return helper(); }\n"
    "int METHOD_DECL(widget, draw)(int x) { return helper() + x; }\n"
)


def _index(
    temp_repo: Path,
    mock_ingestor: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
    preprocess: bool,
    filename: str = "api.c",
    include_dirs: str = "",
) -> None:
    project = temp_repo / PROJECT
    (project / "inc").mkdir(parents=True)
    (project / "inc" / "compiler.h").write_text(HEADER)
    (project / "Makefile").write_text("all:\n\tcc -Iinc -c api.c\n")
    (project / filename).write_text(SOURCE)
    monkeypatch.setattr(settings, "C_PREPROCESS", preprocess)
    monkeypatch.setattr(settings, "C_INCLUDE_DIRS", include_dirs)
    monkeypatch.setattr(settings, "C_DEFINES", "")
    run_updater(project, mock_ingestor, skip_if_missing="c")


def _functions(mock_ingestor: MagicMock) -> dict[str, dict]:
    return {
        call[0][1][cs.KEY_QUALIFIED_NAME]: call[0][1]
        for call in get_nodes(mock_ingestor, cs.NodeLabel.FUNCTION)
    }


def _function_line(mock_ingestor: MagicMock, name: str) -> int:
    lines = [
        props[cs.KEY_START_LINE]
        for qn, props in _functions(mock_ingestor).items()
        if qn.endswith(f".api.{name}")
    ]
    assert len(lines) == 1, (name, sorted(_functions(mock_ingestor)))
    return lines[0]


def _function_names(mock_ingestor: MagicMock) -> set[str]:
    return {
        call[0][1][cs.KEY_QUALIFIED_NAME].rsplit(".", 1)[-1]
        for call in get_nodes(mock_ingestor, cs.NodeLabel.FUNCTION)
    }


def _callers_of_helper(mock_ingestor: MagicMock) -> set[str]:
    return {
        c.args[0][2].rsplit(".", 1)[-1]
        for c in get_relationships(mock_ingestor, cs.RelationshipType.CALLS)
        if c.args[2][2].endswith(".helper")
    }


def test_token_pasted_method_name_is_indexed_when_preprocessing(
    temp_repo: Path, mock_ingestor: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    _index(temp_repo, mock_ingestor, monkeypatch, preprocess=True)
    names = _function_names(mock_ingestor)
    assert "_M_widget_draw" in names
    assert "PlainEntry" in names
    assert {"_M_widget_draw", "PlainEntry"} <= _callers_of_helper(mock_ingestor)


def test_token_pasted_method_name_is_absent_without_preprocessing(
    temp_repo: Path, mock_ingestor: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    _index(temp_repo, mock_ingestor, monkeypatch, preprocess=False)
    assert "_M_widget_draw" not in _function_names(mock_ingestor)


def test_uppercase_c_extension_is_parsed_as_c(
    temp_repo: Path, mock_ingestor: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    _index(temp_repo, mock_ingestor, monkeypatch, preprocess=False, filename="API.C")
    assert "helper" in _function_names(mock_ingestor)


def test_settings_build_preprocessor_with_flags(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from codebase_rag.parsers.c_preprocessor import CPreprocessor

    (tmp_path / "extra").mkdir()
    monkeypatch.setattr(settings, "C_PREPROCESS", False)
    assert CPreprocessor.from_settings(tmp_path) is None
    monkeypatch.setattr(settings, "C_PREPROCESS", True)
    monkeypatch.setattr(settings, "C_INCLUDE_DIRS", "extra, missing")
    monkeypatch.setattr(settings, "C_DEFINES", "A=1,B=")
    pp = CPreprocessor.from_settings(tmp_path)
    assert pp is not None
    assert pp.include_dirs == [tmp_path / "extra"]
    assert pp.defines == {"A": "1", "B": ""}


def test_header_definitions_are_not_indexed_under_the_c_file(
    temp_repo: Path, mock_ingestor: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    _index(temp_repo, mock_ingestor, monkeypatch, preprocess=True)
    pasted = [
        qn for qn in _functions(mock_ingestor) if qn.endswith(".api.header_helper")
    ]
    assert pasted == []


def test_preprocessed_functions_keep_their_source_lines(
    temp_repo: Path, mock_ingestor: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    _index(temp_repo, mock_ingestor, monkeypatch, preprocess=True)
    assert _function_line(mock_ingestor, "helper") == 2
    assert _function_line(mock_ingestor, "PlainEntry") == 3
    assert _function_line(mock_ingestor, "_M_widget_draw") == 4


def test_c_file_whose_directory_is_an_include_dir_still_expands(
    temp_repo: Path, mock_ingestor: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The file is then reachable through an include directory, and pcpp may
    # name it in its line markers by a different spelling of the same path.
    _index(temp_repo, mock_ingestor, monkeypatch, preprocess=True, include_dirs=".")
    assert _function_line(mock_ingestor, "_M_widget_draw") == 4
    assert _function_line(mock_ingestor, "PlainEntry") == 3
