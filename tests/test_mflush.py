"""Tests for the --mflush feature."""

import logging
import os
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import kape_modules


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_dirs(tmp_path):
    """Create temporary source and destination directories."""
    src = tmp_path / "source"
    src.mkdir()
    (src / "testfile.txt").write_text("hello")
    dest = tmp_path / "dest"
    dest.mkdir()
    return src, dest


@pytest.fixture
def sample_module_dir(tmp_path):
    """Create a minimal Modules directory with one sample module."""
    modules = tmp_path / "Modules"
    sample = modules / "Sample"
    sample.mkdir(parents=True)
    mkape = sample / "TestMod.mkape"
    mkape.write_text(
        "Description: Test module\n"
        "Category: TestCat\n"
        "Author: test\n"
        "Version: 1\n"
        "Id: 00000000-0000-0000-0000-000000000099\n"
        "ExportFormat: csv\n"
        "Processors:\n"
        "  - Executable: echo\n"
        '    CommandLine: "hello"\n'
        "    ExportFormat: csv\n"
    )
    return modules


# ---------------------------------------------------------------------------
# Argument parser tests
# ---------------------------------------------------------------------------


class TestMflushArgument:
    """Verify the --mflush argument is parsed correctly."""

    def test_default_is_true(self):
        parser = kape_modules.build_argument_parser()
        args = parser.parse_args(["--msource", "/s", "--mdest", "/d", "--module", "X"])
        assert args.mflush is True

    def test_explicit_mflush(self):
        parser = kape_modules.build_argument_parser()
        args = parser.parse_args(
            ["--mflush", "--msource", "/s", "--mdest", "/d", "--module", "X"]
        )
        assert args.mflush is True

    def test_no_mflush(self):
        parser = kape_modules.build_argument_parser()
        args = parser.parse_args(
            ["--no-mflush", "--msource", "/s", "--mdest", "/d", "--module", "X"]
        )
        assert args.mflush is False


# ---------------------------------------------------------------------------
# Flush behaviour tests
# ---------------------------------------------------------------------------


class TestMflushExecution:
    """Verify that --mflush deletes the destination directory."""

    def test_mflush_removes_existing_mdest(
        self, tmp_dirs, sample_module_dir, caplog
    ):
        """With --mflush (default), an existing mdest is removed and recreated."""
        src, dest = tmp_dirs
        # Place a pre-existing file in dest
        marker = dest / "old_output.txt"
        marker.write_text("stale data")
        assert marker.exists()

        with patch("subprocess.run"), caplog.at_level(logging.INFO):
            kape_modules.main(
                [
                    "--msource", str(src),
                    "--mdest", str(dest),
                    "--module", "TestMod",
                    "--mpath", str(sample_module_dir),
                ]
            )

        # The old file should have been removed
        assert not marker.exists()
        # But the dest directory itself should be recreated
        assert dest.is_dir()
        assert "--mflush" in caplog.text

    def test_no_mflush_preserves_existing_mdest(
        self, tmp_dirs, sample_module_dir
    ):
        """With --no-mflush, existing files in mdest are preserved."""
        src, dest = tmp_dirs
        marker = dest / "old_output.txt"
        marker.write_text("keep me")

        with patch("subprocess.run"):
            kape_modules.main(
                [
                    "--no-mflush",
                    "--msource", str(src),
                    "--mdest", str(dest),
                    "--module", "TestMod",
                    "--mpath", str(sample_module_dir),
                ]
            )

        # The old file should still be present
        assert marker.exists()

    def test_mflush_nonexistent_mdest_no_error(
        self, tmp_path, sample_module_dir
    ):
        """mflush does not fail when mdest does not yet exist."""
        src = tmp_path / "source"
        src.mkdir()
        (src / "testfile.txt").write_text("hello")
        dest = tmp_path / "new_dest"
        assert not dest.exists()

        with patch("subprocess.run"):
            kape_modules.main(
                [
                    "--mflush",
                    "--msource", str(src),
                    "--mdest", str(dest),
                    "--module", "TestMod",
                    "--mpath", str(sample_module_dir),
                ]
            )

        # Dest should be created even though it didn't exist before
        assert dest.is_dir()

    def test_mflush_skipped_on_dry_run(
        self, tmp_dirs, sample_module_dir
    ):
        """--dry-run should prevent mflush from deleting anything."""
        src, dest = tmp_dirs
        marker = dest / "old_output.txt"
        marker.write_text("keep me")

        with patch("subprocess.run"):
            kape_modules.main(
                [
                    "--mflush",
                    "--dry-run",
                    "--msource", str(src),
                    "--mdest", str(dest),
                    "--module", "TestMod",
                    "--mpath", str(sample_module_dir),
                ]
            )

        # dry-run should not delete anything
        assert marker.exists()

    def test_mflush_removes_nested_contents(
        self, tmp_dirs, sample_module_dir
    ):
        """mflush removes subdirectories and files within mdest."""
        src, dest = tmp_dirs
        sub = dest / "subdir" / "nested"
        sub.mkdir(parents=True)
        (sub / "deep.txt").write_text("deep file")
        (dest / "top.txt").write_text("top file")

        with patch("subprocess.run"):
            kape_modules.main(
                [
                    "--msource", str(src),
                    "--mdest", str(dest),
                    "--module", "TestMod",
                    "--mpath", str(sample_module_dir),
                ]
            )

        # All old contents should be gone
        assert not (dest / "top.txt").exists()
        assert not (dest / "subdir").exists()
        # dest itself should be recreated
        assert dest.is_dir()

    def test_mflush_rmtree_failure_exits(
        self, tmp_dirs, sample_module_dir
    ):
        """When shutil.rmtree fails, main should exit with an error."""
        src, dest = tmp_dirs

        with patch("subprocess.run"), \
             patch("shutil.rmtree", side_effect=OSError("Permission denied")), \
             pytest.raises(SystemExit):
            kape_modules.main(
                [
                    "--msource", str(src),
                    "--mdest", str(dest),
                    "--module", "TestMod",
                    "--mpath", str(sample_module_dir),
                ]
            )
