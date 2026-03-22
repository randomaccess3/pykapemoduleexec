"""Tests for the --dry-run feature."""

import logging
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

# Ensure the parent package is importable
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
    # dest is intentionally NOT created – dry-run should not create it either
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
        "Id: 00000000-0000-0000-0000-000000000001\n"
        "ExportFormat: csv\n"
        "Processors:\n"
        "  - Executable: echo\n"
        '    CommandLine: "%sourceDirectory%"\n'
        "    ExportFormat: csv\n"
        "    ExportFile: out.csv\n"
    )
    return modules


@pytest.fixture
def sample_module_with_filemask(tmp_path):
    """Module with a FileMask to exercise per-file dry-run logging."""
    modules = tmp_path / "Modules"
    sample = modules / "Sample"
    sample.mkdir(parents=True)
    mkape = sample / "MaskMod.mkape"
    mkape.write_text(
        "Description: Mask module\n"
        "Category: MaskCat\n"
        "Author: test\n"
        "Version: 1\n"
        "Id: 00000000-0000-0000-0000-000000000002\n"
        "ExportFormat: csv\n"
        "FileMask: '*.txt'\n"
        "Processors:\n"
        "  - Executable: cat\n"
        '    CommandLine: "%sourceFile%"\n'
        "    ExportFormat: csv\n"
    )
    return modules


# ---------------------------------------------------------------------------
# execute_command dry-run tests
# ---------------------------------------------------------------------------


class TestExecuteCommandDryRun:
    """execute_command must log but never run anything when dry_run=True."""

    def test_no_subprocess_call(self):
        """subprocess.run must not be called during dry-run."""
        with patch("kape_modules.subprocess.run") as mock_run:
            kape_modules.execute_command(
                executable="echo",
                cmdline="hello",
                dest_dir="/tmp/fake",
                export_file=None,
                append=False,
                wait_timeout=0,
                debug=False,
                dry_run=True,
            )
            mock_run.assert_not_called()

    def test_logs_would_execute(self, caplog):
        """Dry-run must include '[DRY RUN] Would execute' in log output."""
        with caplog.at_level(logging.INFO):
            kape_modules.execute_command(
                executable="echo",
                cmdline="hello world",
                dest_dir="/tmp/fake",
                export_file=None,
                append=False,
                wait_timeout=0,
                debug=False,
                dry_run=True,
            )
        assert any("[DRY RUN] Would execute" in r.message for r in caplog.records)

    def test_logs_export_file(self, caplog):
        """When export_file is set, dry-run logs the output path."""
        with caplog.at_level(logging.INFO):
            kape_modules.execute_command(
                executable="echo",
                cmdline="hello",
                dest_dir="/tmp/fake",
                export_file="results.csv",
                append=False,
                wait_timeout=0,
                debug=False,
                dry_run=True,
            )
        messages = [r.message for r in caplog.records]
        assert any("results.csv" in m and "[DRY RUN]" in m for m in messages)

    def test_no_file_created(self, tmp_path):
        """Dry-run must not create any files."""
        dest = str(tmp_path / "output")
        os.makedirs(dest, exist_ok=True)
        kape_modules.execute_command(
            executable="echo",
            cmdline="hello",
            dest_dir=dest,
            export_file="output.txt",
            append=False,
            wait_timeout=0,
            debug=False,
            dry_run=True,
        )
        assert not os.path.exists(os.path.join(dest, "output.txt"))


# ---------------------------------------------------------------------------
# run_module dry-run tests
# ---------------------------------------------------------------------------


class TestRunModuleDryRun:
    """run_module with dry_run=True must not execute anything or create dirs."""

    def test_no_dest_dir_created(self, tmp_dirs, sample_module_dir):
        src, dest = tmp_dirs
        module_file = sample_module_dir / "Sample" / "TestMod.mkape"
        module_data = kape_modules.load_module(module_file)

        with patch("kape_modules.subprocess.run") as mock_run:
            kape_modules.run_module(
                module_name="TestMod",
                module_data=module_data,
                module_file=module_file,
                modules_dir=sample_module_dir,
                msource=str(src),
                mdest=str(dest),
                mef=None,
                mvars={},
                debug=False,
                processed_ids=set(),
                dry_run=True,
            )
            mock_run.assert_not_called()
        # The category sub-directory should NOT be created
        assert not (dest / "TestCat").exists()

    def test_filemask_dry_run(self, tmp_dirs, sample_module_with_filemask, caplog):
        """FileMask modules log once per matched file during dry-run."""
        src, dest = tmp_dirs
        module_file = sample_module_with_filemask / "Sample" / "MaskMod.mkape"
        module_data = kape_modules.load_module(module_file)

        with caplog.at_level(logging.INFO):
            with patch("kape_modules.subprocess.run") as mock_run:
                kape_modules.run_module(
                    module_name="MaskMod",
                    module_data=module_data,
                    module_file=module_file,
                    modules_dir=sample_module_with_filemask,
                    msource=str(src),
                    mdest=str(dest),
                    mef=None,
                    mvars={},
                    debug=False,
                    processed_ids=set(),
                    dry_run=True,
                )
                mock_run.assert_not_called()
        dry_msgs = [r.message for r in caplog.records if "[DRY RUN]" in r.message]
        assert len(dry_msgs) >= 1  # at least one matched file


# ---------------------------------------------------------------------------
# CLI integration tests
# ---------------------------------------------------------------------------


class TestMainDryRun:
    """End-to-end: main() with --dry-run must not create output or run commands."""

    def test_main_dry_run(self, tmp_path):
        src = tmp_path / "source"
        src.mkdir()
        (src / "sample.txt").write_text("data")
        dest = tmp_path / "output"

        modules = tmp_path / "Modules"
        sample = modules / "Sample"
        sample.mkdir(parents=True)
        (sample / "QuickMod.mkape").write_text(
            "Description: Quick\n"
            "Category: QuickCat\n"
            "Author: test\n"
            "Version: 1\n"
            "Id: 00000000-0000-0000-0000-000000000099\n"
            "ExportFormat: csv\n"
            "Processors:\n"
            "  - Executable: echo\n"
            '    CommandLine: "hello"\n'
            "    ExportFormat: csv\n"
        )

        with patch("kape_modules.subprocess.run") as mock_run, \
             patch("kape_modules.Path.__file__", tmp_path, create=True):
            kape_modules.main([
                "--msource", str(src),
                "--mdest", str(dest),
                "--module", "QuickMod",
                "--mpath", str(modules),
                "--dry-run",
            ])
            mock_run.assert_not_called()

        # dest directory should not have been created
        assert not dest.exists()
