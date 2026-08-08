"""Atomic JSON writes: the previous file must survive a failed write."""
import json
import os

import pytest

from wsjdaily.jsonio import write_json


def test_writes_readable_json(tmp_path) -> None:
    path = str(tmp_path / "out.json")
    write_json(path, {"a": 1, "b": ["x", "y"]})
    assert json.loads(open(path).read()) == {"a": 1, "b": ["x", "y"]}


def test_overwrites_an_existing_file(tmp_path) -> None:
    path = str(tmp_path / "out.json")
    write_json(path, {"v": 1})
    write_json(path, {"v": 2})
    assert json.loads(open(path).read()) == {"v": 2}


def test_a_failed_write_leaves_the_previous_file_intact(tmp_path) -> None:
    """The whole point. A plain open(path, "w") truncates before writing, so a
    failure mid-serialisation would leave a partial file that the GitHub
    workflow would then commit over a previously-good one."""
    path = str(tmp_path / "out.json")
    write_json(path, {"good": True})
    with pytest.raises(TypeError):
        write_json(path, {"bad": object()})     # not JSON-serialisable
    assert json.loads(open(path).read()) == {"good": True}


def test_a_failed_write_leaves_no_temp_files_behind(tmp_path) -> None:
    path = str(tmp_path / "out.json")
    with pytest.raises(TypeError):
        write_json(path, {"bad": object()})
    assert os.listdir(tmp_path) == [], "temp file should have been cleaned up"


def test_a_successful_write_leaves_no_temp_files_behind(tmp_path) -> None:
    path = str(tmp_path / "out.json")
    write_json(path, {"a": 1})
    assert os.listdir(tmp_path) == ["out.json"]


def test_writes_unicode_unescaped(tmp_path) -> None:
    """ensure_ascii=False must be preserved -- headlines carry curly quotes."""
    path = str(tmp_path / "out.json")
    write_json(path, {"t": "Trump’s Tariffs"})
    assert "’" in open(path, encoding="utf-8").read()
