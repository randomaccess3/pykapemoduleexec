"""Tests for the --mthreads multithreading feature."""

import logging
import os
from pathlib import Path
from unittest.mock import patch, call

import pytest

import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import kape_modules


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_dirs(tmp_path):
    """Create temporary source (with multiple files) and destination directories."""
    src = tmp_path / "source"
    src.mkdir()
    for i in range(5):
        (src / f"file{i}.txt").write_text(f"content-{i}")
    dest = tmp_path / "dest"
    dest.mkdir()
    return src, dest


@pytest.fixture
def filemask_module_dir(tmp_path):
    """Create a module with a FileMask that matches multiple .txt files."""
    modules = tmp_path / "Modules"
    sample = modules / "Sample"
    sample.mkdir(parents=True)
    mkape = sample / "ThreadMod.mkape"
    mkape.write_text(
        "Description: Thread test module\n"
        "Category: ThreadCat\n"
        "Author: test\n"
        "Version: 1\n"
        "Id: 00000000-0000-0000-0000-100000000001\n"
        "ExportFormat: csv\n"
        "FileMask: '*.txt'\n"
        "Processors:\n"
        "  - Executable: echo\n"
        '    CommandLine: "%sourceFile%"\n'
        "    ExportFormat: csv\n"
    )
    return modules


@pytest.fixture
def two_module_dir(tmp_path):
    """Create two independent modules for parallel execution tests."""
    modules = tmp_path / "Modules"
    sample = modules / "Sample"
    sample.mkdir(parents=True)

    (sample / "ModA.mkape").write_text(
        "Description: Module A\n"
        "Category: CatA\n"
        "Author: test\n"
        "Version: 1\n"
        "Id: 00000000-0000-0000-0000-200000000001\n"
        "ExportFormat: csv\n"
        "Processors:\n"
        "  - Executable: echo\n"
        '    CommandLine: "modA"\n'
        "    ExportFormat: csv\n"
    )
    (sample / "ModB.mkape").write_text(
        "Description: Module B\n"
        "Category: CatB\n"
        "Author: test\n"
        "Version: 1\n"
        "Id: 00000000-0000-0000-0000-200000000002\n"
        "ExportFormat: csv\n"
        "Processors:\n"
        "  - Executable: echo\n"
        '    CommandLine: "modB"\n'
        "    ExportFormat: csv\n"
    )
    return modules


# ---------------------------------------------------------------------------
# CLI argument tests
# ---------------------------------------------------------------------------


class TestMthreadsArgument:
    """--mthreads argument is recognized and has the correct default."""

    def test_default_is_one(self):
        parser = kape_modules.build_argument_parser()
        args = parser.parse_args([
            "--msource", "/tmp/s", "--mdest", "/tmp/d", "--module", "X",
        ])
        assert args.mthreads == 1

    def test_custom_value(self):
        parser = kape_modules.build_argument_parser()
        args = parser.parse_args([
            "--msource", "/tmp/s", "--mdest", "/tmp/d", "--module", "X",
            "--mthreads", "4",
        ])
        assert args.mthreads == 4


# ---------------------------------------------------------------------------
# run_module multithreading tests (FileMask)
# ---------------------------------------------------------------------------


class TestRunModuleThreading:
    """run_module uses ThreadPoolExecutor when num_threads > 1 with FileMask."""

    def test_filemask_all_files_processed_with_threads(
        self, tmp_dirs, filemask_module_dir
    ):
        """All matching files are processed even when using multiple threads."""
        src, dest = tmp_dirs
        module_file = filemask_module_dir / "Sample" / "ThreadMod.mkape"
        module_data = kape_modules.load_module(module_file)

        with patch("kape_modules.subprocess.run") as mock_run:
            mock_run.return_value = type("R", (), {"returncode": 0})()
            kape_modules.run_module(
                module_name="ThreadMod",
                module_data=module_data,
                module_file=module_file,
                modules_dir=filemask_module_dir,
                msource=str(src),
                mdest=str(dest),
                mef=None,
                mvars={},
                debug=False,
                processed_ids=set(),
                dry_run=False,
                num_threads=3,
            )
        # There are 5 .txt files; each should trigger one subprocess call
        assert mock_run.call_count == 5

    def test_filemask_serial_all_files_processed(
        self, tmp_dirs, filemask_module_dir
    ):
        """Same module with num_threads=1 still processes all files."""
        src, dest = tmp_dirs
        module_file = filemask_module_dir / "Sample" / "ThreadMod.mkape"
        module_data = kape_modules.load_module(module_file)

        with patch("kape_modules.subprocess.run") as mock_run:
            mock_run.return_value = type("R", (), {"returncode": 0})()
            kape_modules.run_module(
                module_name="ThreadMod",
                module_data=module_data,
                module_file=module_file,
                modules_dir=filemask_module_dir,
                msource=str(src),
                mdest=str(dest),
                mef=None,
                mvars={},
                debug=False,
                processed_ids=set(),
                dry_run=False,
                num_threads=1,
            )
        assert mock_run.call_count == 5

    def test_filemask_dry_run_with_threads(
        self, tmp_dirs, filemask_module_dir, caplog
    ):
        """Dry-run with num_threads > 1 still logs without executing."""
        src, dest = tmp_dirs
        module_file = filemask_module_dir / "Sample" / "ThreadMod.mkape"
        module_data = kape_modules.load_module(module_file)

        with caplog.at_level(logging.INFO):
            with patch("kape_modules.subprocess.run") as mock_run:
                kape_modules.run_module(
                    module_name="ThreadMod",
                    module_data=module_data,
                    module_file=module_file,
                    modules_dir=filemask_module_dir,
                    msource=str(src),
                    mdest=str(dest),
                    mef=None,
                    mvars={},
                    debug=False,
                    processed_ids=set(),
                    dry_run=True,
                    num_threads=4,
                )
                mock_run.assert_not_called()

        dry_msgs = [r.message for r in caplog.records if "[DRY RUN]" in r.message]
        assert len(dry_msgs) >= 5  # one per matched file


# ---------------------------------------------------------------------------
# main() multithreading tests
# ---------------------------------------------------------------------------


class TestMainThreading:
    """main() passes num_threads and parallelizes when --mthreads > 1."""

    def test_main_mthreads_passed_to_run_module(self, tmp_path):
        """run_module receives the num_threads argument from --mthreads."""
        src = tmp_path / "source"
        src.mkdir()
        (src / "data.txt").write_text("data")
        dest = tmp_path / "dest"

        modules = tmp_path / "Modules"
        sample = modules / "Sample"
        sample.mkdir(parents=True)
        (sample / "Quick.mkape").write_text(
            "Description: Quick\n"
            "Category: QuickCat\n"
            "Author: test\n"
            "Version: 1\n"
            "Id: 00000000-0000-0000-0000-300000000001\n"
            "ExportFormat: csv\n"
            "Processors:\n"
            "  - Executable: echo\n"
            '    CommandLine: "hello"\n'
            "    ExportFormat: csv\n"
        )

        with patch("kape_modules.run_module") as mock_run_module:
            kape_modules.main([
                "--msource", str(src),
                "--mdest", str(dest),
                "--module", "Quick",
                "--mpath", str(modules),
                "--mthreads", "4",
                "--dry-run",
            ])
            mock_run_module.assert_called_once()
            _, kwargs = mock_run_module.call_args
            # num_threads is passed as a positional or keyword arg
            call_args = mock_run_module.call_args
            # Check both args and kwargs for num_threads value
            all_args = list(call_args.args) if call_args.args else []
            all_kwargs = call_args.kwargs if call_args.kwargs else {}
            assert all_kwargs.get("num_threads") == 4 or 4 in all_args

    def test_main_multiple_modules_with_threads(self, tmp_path):
        """Multiple modules execute via ThreadPoolExecutor when threads > 1."""
        src = tmp_path / "source"
        src.mkdir()
        dest = tmp_path / "dest"

        modules = tmp_path / "Modules"
        sample = modules / "Sample"
        sample.mkdir(parents=True)
        for name in ("AlphaM", "BetaM"):
            (sample / f"{name}.mkape").write_text(
                f"Description: {name}\n"
                f"Category: Cat{name}\n"
                "Author: test\n"
                "Version: 1\n"
                f"Id: 00000000-0000-0000-0000-40000000000{'1' if name == 'AlphaM' else '2'}\n"
                "ExportFormat: csv\n"
                "Processors:\n"
                "  - Executable: echo\n"
                f'    CommandLine: "{name}"\n'
                "    ExportFormat: csv\n"
            )

        with patch("kape_modules.subprocess.run") as mock_run:
            mock_run.return_value = type("R", (), {"returncode": 0})()
            kape_modules.main([
                "--msource", str(src),
                "--mdest", str(dest),
                "--module", "AlphaM,BetaM",
                "--mpath", str(modules),
                "--mthreads", "2",
            ])
        # Both modules should have been executed
        assert mock_run.call_count == 2

    def test_main_default_threads_serial(self, tmp_path):
        """Default --mthreads=1 still runs all modules serially."""
        src = tmp_path / "source"
        src.mkdir()
        dest = tmp_path / "dest"

        modules = tmp_path / "Modules"
        sample = modules / "Sample"
        sample.mkdir(parents=True)
        for name in ("SerA", "SerB"):
            (sample / f"{name}.mkape").write_text(
                f"Description: {name}\n"
                f"Category: Cat{name}\n"
                "Author: test\n"
                "Version: 1\n"
                f"Id: 00000000-0000-0000-0000-50000000000{'1' if name == 'SerA' else '2'}\n"
                "ExportFormat: csv\n"
                "Processors:\n"
                "  - Executable: echo\n"
                f'    CommandLine: "{name}"\n'
                "    ExportFormat: csv\n"
            )

        with patch("kape_modules.subprocess.run") as mock_run:
            mock_run.return_value = type("R", (), {"returncode": 0})()
            kape_modules.main([
                "--msource", str(src),
                "--mdest", str(dest),
                "--module", "SerA,SerB",
                "--mpath", str(modules),
            ])
        assert mock_run.call_count == 2
