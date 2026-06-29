"""Tests for FileMask glob processing behavior."""

from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import kape_modules


def test_filemask_supports_pipe_separated_globs_and_url_encoded_values(tmp_path):
    src = tmp_path / "source"
    src.mkdir()

    expected = [
        src / "$UsnJrnl:$J",
        src / "$J",
        src / "UsnJrnl-J",
        src / "$UsnJrnl_001.bin",
    ]
    for file_path in expected:
        file_path.write_text("x")
    (src / "ignore.bin").write_text("x")

    matches = kape_modules.find_matching_files(
        src, "$UsnJrnl%3A$J|$J|UsnJrnl-J|$UsnJrnl_*.bin"
    )

    assert {p.name for p in matches} == {p.name for p in expected}
