"""Tests for FileMask glob processing behavior."""

from pathlib import Path

import os
import subprocess
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import kape_modules


def test_filemask_supports_pipe_separated_globs_and_url_encoded_values(tmp_path):
    src = tmp_path / "source"
    src.mkdir()

    expected = [
        src / "$J",
        src / "UsnJrnl-J",
        src / "$UsnJrnl_001.bin",
    ]
    if sys.platform != "win32":
        expected.append(src / "$UsnJrnl:$J")
    for file_path in expected:
        file_path.write_text("x")
    (src / "ignore.bin").write_text("x")

    matches = kape_modules.find_matching_files(
        src, "$UsnJrnl%3A$J|$J|UsnJrnl%2DJ|$UsnJrnl_*.bin"
    )

    assert {p.name for p in matches} == {p.name for p in expected}


def test_filemask_with_only_empty_pipe_entries_returns_no_matches(tmp_path):
    src = tmp_path / "source"
    src.mkdir()
    (src / "file.txt").write_text("x")

    matches = kape_modules.find_matching_files(src, " | || ")

    assert matches == []


def test_literal_filemask_only_matches_enumerated_files(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    metadata = source / "$MFT"
    metadata.write_text("not returned by directory enumeration")
    visible = source / "visible.txt"
    visible.write_text("enumerated")
    enumerations = []

    def walk(path, *, followlinks, onerror):
        assert path == source
        assert not followlinks
        enumerations.append(path)
        return iter([(str(source), [], [visible.name])])

    monkeypatch.setattr(os, "walk", walk)

    assert kape_modules.find_matching_files(source, "$MFT") == []
    assert enumerations == [source]


def test_literal_filemask_still_matches_collected_metadata_file(tmp_path):
    source = tmp_path / "source"
    nested = source / "collected" / "C"
    nested.mkdir(parents=True)
    metadata = nested / "$MFT"
    metadata.write_text("collected file")

    assert kape_modules.find_matching_files(source, "$MFT") == [metadata]


@pytest.mark.skipif(sys.platform != "win32", reason="Windows directory junctions")
@pytest.mark.parametrize("mask", ["*.db", "regex:.*[.]db"])
def test_filemask_does_not_follow_directory_junctions(tmp_path, mask):
    source = tmp_path / "source"
    original = source / "original"
    original.mkdir(parents=True)
    database = original / "ActivitiesCache.db"
    database.write_text("artifact")
    junction = source / "alias"
    subprocess.run(
        [os.environ["COMSPEC"], "/c", "mklink", "/J", str(junction), str(original)],
        check=True,
        capture_output=True,
    )
    try:
        assert kape_modules.find_matching_files(source, mask) == [database]
    finally:
        junction.rmdir()


def test_filemask_reports_directory_enumeration_errors(tmp_path, monkeypatch, caplog):
    error = PermissionError(13, "Access denied", str(tmp_path / "unreadable"))

    def walk(path, *, followlinks, onerror):
        onerror(error)
        return iter([])

    monkeypatch.setattr(os, "walk", walk)

    assert kape_modules.find_matching_files(tmp_path, "*.db") == []
    assert "Cannot enumerate source path" in caplog.text
    assert "unreadable" in caplog.text
