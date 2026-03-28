"""Tests for console.log file logging and debug process output capture."""

import logging
import os
import re
from pathlib import Path
from unittest.mock import patch

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
    (sample / "LogMod.mkape").write_text(
        "Description: Log test module\n"
        "Category: LogCat\n"
        "Author: test\n"
        "Version: 1\n"
        "Id: 00000000-0000-0000-0000-000000000a01\n"
        "ExportFormat: csv\n"
        "Processors:\n"
        "  - Executable: echo\n"
        '    CommandLine: "hello"\n'
        "    ExportFormat: csv\n"
    )
    return modules


@pytest.fixture
def capture_console_log():
    """Wrap ``_setup_console_log`` to capture the log path it generates.

    Returns a dict that will contain ``"log_path"`` after ``main()`` runs.
    The fixture automatically patches ``_setup_console_log`` for the test.
    """
    captured = {}
    original = kape_modules._setup_console_log

    def _wrap(mdest, debug):
        handler, log_path = original(mdest, debug)
        captured["log_path"] = log_path
        return handler, log_path

    with patch("kape_modules._setup_console_log", side_effect=_wrap):
        yield captured


# ---------------------------------------------------------------------------
# _HighResFormatter tests
# ---------------------------------------------------------------------------


class TestHighResFormatter:
    """_HighResFormatter must produce microsecond-precision timestamps."""

    def test_format_includes_microseconds(self):
        fmt = kape_modules._HighResFormatter("%(asctime)s [%(levelname)s] %(message)s")
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg="hello", args=(), exc_info=None,
        )
        formatted = fmt.format(record)
        # Expect a pattern like 2024-01-15T10:30:45.123456
        assert re.search(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}", formatted)

    def test_format_includes_level_and_message(self):
        fmt = kape_modules._HighResFormatter("%(asctime)s [%(levelname)s] %(message)s")
        record = logging.LogRecord(
            name="test", level=logging.WARNING, pathname="", lineno=0,
            msg="something broke", args=(), exc_info=None,
        )
        formatted = fmt.format(record)
        assert "[WARNING]" in formatted
        assert "something broke" in formatted


# ---------------------------------------------------------------------------
# _setup_console_log tests
# ---------------------------------------------------------------------------


class TestSetupConsoleLog:
    """_setup_console_log adds a file handler writing to console.log."""

    def test_creates_console_log_file(self, tmp_path):
        dest = str(tmp_path / "output")
        os.makedirs(dest, exist_ok=True)
        root = logging.getLogger()
        old_level = root.level
        root.setLevel(logging.INFO)
        handler, log_path = kape_modules._setup_console_log(dest, debug=False)
        try:
            logging.info("console log test message")
            handler.flush()
            assert os.path.exists(log_path)
            content = open(log_path).read()
            assert "console log test message" in content
        finally:
            root.setLevel(old_level)
            logging.getLogger().removeHandler(handler)
            handler.close()

    def test_console_log_filename_format(self, tmp_path):
        dest = str(tmp_path / "output")
        os.makedirs(dest, exist_ok=True)
        handler, log_path = kape_modules._setup_console_log(dest, debug=False)
        try:
            filename = os.path.basename(log_path)
            # Expect format: yyyy-MM-ddTHH_mm_ss_fffffff_console.log
            assert re.match(
                r"\d{4}-\d{2}-\d{2}T\d{2}_\d{2}_\d{2}_\d{7}_console\.log$",
                filename,
            )
        finally:
            logging.getLogger().removeHandler(handler)
            handler.close()

    def test_high_res_timestamps_in_file(self, tmp_path):
        dest = str(tmp_path / "output")
        os.makedirs(dest, exist_ok=True)
        root = logging.getLogger()
        old_level = root.level
        root.setLevel(logging.INFO)
        handler, log_path = kape_modules._setup_console_log(dest, debug=False)
        try:
            logging.info("timestamp test")
            handler.flush()
            content = open(log_path).read()
            # Microsecond-precision timestamp
            assert re.search(
                r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}", content
            )
        finally:
            root.setLevel(old_level)
            logging.getLogger().removeHandler(handler)
            handler.close()

    def test_debug_level_when_debug_true(self, tmp_path):
        dest = str(tmp_path / "output")
        os.makedirs(dest, exist_ok=True)
        root = logging.getLogger()
        old_level = root.level
        root.setLevel(logging.DEBUG)
        handler, log_path = kape_modules._setup_console_log(dest, debug=True)
        try:
            logging.debug("debug visible")
            handler.flush()
            content = open(log_path).read()
            assert "debug visible" in content
        finally:
            root.setLevel(old_level)
            logging.getLogger().removeHandler(handler)
            handler.close()

    def test_info_level_when_debug_false(self, tmp_path):
        dest = str(tmp_path / "output")
        os.makedirs(dest, exist_ok=True)
        root = logging.getLogger()
        old_level = root.level
        root.setLevel(logging.DEBUG)
        handler, log_path = kape_modules._setup_console_log(dest, debug=False)
        try:
            logging.debug("debug hidden")
            logging.info("info visible")
            handler.flush()
            content = open(log_path).read()
            assert "debug hidden" not in content
            assert "info visible" in content
        finally:
            root.setLevel(old_level)
            logging.getLogger().removeHandler(handler)
            handler.close()


# ---------------------------------------------------------------------------
# Console log created by main() tests
# ---------------------------------------------------------------------------


class TestMainConsoleLog:
    """main() creates a timestamped console log in mdest during module execution."""

    def test_console_log_created_in_mdest(
        self, tmp_dirs, sample_module_dir, capture_console_log
    ):
        src, dest = tmp_dirs
        with patch("kape_modules.subprocess.run") as mock_run:
            mock_run.return_value = type(
                "R", (), {"returncode": 0, "stdout": b"", "stderr": b""}
            )()
            kape_modules.main([
                "--msource", str(src),
                "--mdest", str(dest),
                "--module", "LogMod",
                "--mpath", str(sample_module_dir),
            ])
        log_path = capture_console_log["log_path"]
        assert os.path.exists(log_path)
        content = open(log_path).read()
        assert "Processing module: LogMod" in content
        assert "Done." in content

    def test_console_log_has_high_res_timestamps(
        self, tmp_dirs, sample_module_dir, capture_console_log
    ):
        src, dest = tmp_dirs
        with patch("kape_modules.subprocess.run") as mock_run:
            mock_run.return_value = type(
                "R", (), {"returncode": 0, "stdout": b"", "stderr": b""}
            )()
            kape_modules.main([
                "--msource", str(src),
                "--mdest", str(dest),
                "--module", "LogMod",
                "--mpath", str(sample_module_dir),
            ])
        content = open(capture_console_log["log_path"]).read()
        assert re.search(
            r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}", content
        )

    def test_no_console_log_in_dry_run(self, tmp_path):
        src = tmp_path / "source"
        src.mkdir()
        (src / "file.txt").write_text("data")
        dest = tmp_path / "dest"

        modules = tmp_path / "Modules"
        sample = modules / "Sample"
        sample.mkdir(parents=True)
        (sample / "DryMod.mkape").write_text(
            "Description: DryMod\n"
            "Category: DryCat\n"
            "Author: test\n"
            "Version: 1\n"
            "Id: 00000000-0000-0000-0000-000000000a02\n"
            "ExportFormat: csv\n"
            "Processors:\n"
            "  - Executable: echo\n"
            '    CommandLine: "dry"\n'
            "    ExportFormat: csv\n"
        )
        with patch("kape_modules.subprocess.run") as mock_run:
            kape_modules.main([
                "--msource", str(src),
                "--mdest", str(dest),
                "--module", "DryMod",
                "--mpath", str(modules),
                "--dry-run",
            ])
        # No console.log or dest directory in dry-run mode
        assert not dest.exists()

    def test_console_log_handler_cleaned_up(self, tmp_dirs, sample_module_dir):
        """After main() returns, the file handler should be removed."""
        src, dest = tmp_dirs
        root = logging.getLogger()
        handlers_before = len(root.handlers)
        with patch("kape_modules.subprocess.run") as mock_run:
            mock_run.return_value = type(
                "R", (), {"returncode": 0, "stdout": b"", "stderr": b""}
            )()
            kape_modules.main([
                "--msource", str(src),
                "--mdest", str(dest),
                "--module", "LogMod",
                "--mpath", str(sample_module_dir),
            ])
        assert len(root.handlers) == handlers_before


# ---------------------------------------------------------------------------
# Debug mode: process stdout/stderr logging
# ---------------------------------------------------------------------------


class TestDebugProcessOutput:
    """In debug mode, subprocess stdout/stderr are logged."""

    def test_debug_logs_stdout(self, caplog):
        with patch("kape_modules.subprocess.run") as mock_run:
            mock_run.return_value = type(
                "R", (),
                {"returncode": 0, "stdout": b"hello world\n", "stderr": b""},
            )()
            with caplog.at_level(logging.DEBUG):
                kape_modules.execute_command(
                    executable="echo",
                    cmdline="hello",
                    dest_dir="/tmp/fake",
                    export_file=None,
                    append=False,
                    wait_timeout=0,
                    debug=True,
                    dry_run=False,
                )
        assert any("[stdout]" in r.message and "hello world" in r.message
                   for r in caplog.records)

    def test_debug_logs_stderr(self, caplog):
        with patch("kape_modules.subprocess.run") as mock_run:
            mock_run.return_value = type(
                "R", (),
                {"returncode": 1, "stdout": b"", "stderr": b"error msg\n"},
            )()
            with caplog.at_level(logging.DEBUG):
                kape_modules.execute_command(
                    executable="echo",
                    cmdline="fail",
                    dest_dir="/tmp/fake",
                    export_file=None,
                    append=False,
                    wait_timeout=0,
                    debug=True,
                    dry_run=False,
                )
        assert any("[stderr]" in r.message and "error msg" in r.message
                   for r in caplog.records)

    def test_no_debug_no_stdout_logged(self, caplog):
        with patch("kape_modules.subprocess.run") as mock_run:
            mock_run.return_value = type(
                "R", (),
                {"returncode": 0, "stdout": b"hidden output\n", "stderr": b""},
            )()
            with caplog.at_level(logging.DEBUG):
                kape_modules.execute_command(
                    executable="echo",
                    cmdline="test",
                    dest_dir="/tmp/fake",
                    export_file=None,
                    append=False,
                    wait_timeout=0,
                    debug=False,
                    dry_run=False,
                )
        assert not any("[stdout]" in r.message for r in caplog.records)

    def test_debug_export_file_logs_and_writes(self, tmp_path, caplog):
        """In debug mode with export_file, output is logged AND written."""
        dest = str(tmp_path / "out")
        os.makedirs(dest)
        with patch("kape_modules.subprocess.run") as mock_run:
            mock_run.return_value = type(
                "R", (),
                {"returncode": 0, "stdout": b"csv,data\n", "stderr": b""},
            )()
            with caplog.at_level(logging.DEBUG):
                kape_modules.execute_command(
                    executable="tool",
                    cmdline="--csv",
                    dest_dir=dest,
                    export_file="results.csv",
                    append=False,
                    wait_timeout=0,
                    debug=True,
                    dry_run=False,
                )
        # Output logged
        assert any("[stdout]" in r.message and "csv,data" in r.message
                   for r in caplog.records)
        # Output also written to file
        export_path = os.path.join(dest, "results.csv")
        assert os.path.exists(export_path)
        assert "csv,data" in open(export_path).read()

    def test_debug_main_includes_process_output_in_console_log(
        self, tmp_dirs, sample_module_dir, capture_console_log
    ):
        """With --debug, process stdout/stderr appear in console.log."""
        src, dest = tmp_dirs
        with patch("kape_modules.subprocess.run") as mock_run:
            mock_run.return_value = type(
                "R", (),
                {"returncode": 0, "stdout": b"module output here\n", "stderr": b""},
            )()
            kape_modules.main([
                "--msource", str(src),
                "--mdest", str(dest),
                "--module", "LogMod",
                "--mpath", str(sample_module_dir),
                "--debug",
            ])
        content = open(capture_console_log["log_path"]).read()
        assert "module output here" in content
