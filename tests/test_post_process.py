"""Tests for the --post-process feature."""

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
    """Create temporary source and destination directories."""
    src = tmp_path / "source"
    src.mkdir()
    (src / "testfile.txt").write_text("hello")
    dest = tmp_path / "dest"
    dest.mkdir()
    return src, dest


@pytest.fixture
def module_and_post_dir(tmp_path):
    """Create a Modules dir with a regular module and a post-process module."""
    modules = tmp_path / "Modules"
    sample = modules / "Sample"
    sample.mkdir(parents=True)

    (sample / "MainMod.mkape").write_text(
        "Description: Main module\n"
        "Category: MainCat\n"
        "Author: test\n"
        "Version: 1\n"
        "Id: 00000000-0000-0000-0000-600000000001\n"
        "ExportFormat: csv\n"
        "Processors:\n"
        "  - Executable: echo\n"
        '    CommandLine: "main"\n'
        "    ExportFormat: csv\n"
    )
    (sample / "PostMod.mkape").write_text(
        "Description: Post-process module\n"
        "Category: PostCat\n"
        "Author: test\n"
        "Version: 1\n"
        "Id: 00000000-0000-0000-0000-600000000002\n"
        "ExportFormat: csv\n"
        "Processors:\n"
        "  - Executable: echo\n"
        '    CommandLine: "post"\n'
        "    ExportFormat: csv\n"
    )
    return modules


# ---------------------------------------------------------------------------
# Argument parsing tests
# ---------------------------------------------------------------------------


class TestPostProcessArgument:
    """--post-process argument is recognized and parsed correctly."""

    def test_default_is_none(self):
        parser = kape_modules.build_argument_parser()
        args = parser.parse_args([
            "--msource", "/tmp/s", "--mdest", "/tmp/d", "--module", "X",
        ])
        assert args.post_process is None

    def test_single_module(self):
        parser = kape_modules.build_argument_parser()
        args = parser.parse_args([
            "--msource", "/tmp/s", "--mdest", "/tmp/d", "--module", "X",
            "--post-process", "PostMod",
        ])
        assert args.post_process == "PostMod"

    def test_multiple_modules(self):
        parser = kape_modules.build_argument_parser()
        args = parser.parse_args([
            "--msource", "/tmp/s", "--mdest", "/tmp/d", "--module", "X",
            "--post-process", "PostA,PostB",
        ])
        assert args.post_process == "PostA,PostB"


# ---------------------------------------------------------------------------
# Execution order tests
# ---------------------------------------------------------------------------


class TestPostProcessExecution:
    """Post-process modules run after all --module modules have completed."""

    def test_post_process_runs_after_main_modules(
        self, tmp_dirs, module_and_post_dir
    ):
        """Post-process modules execute after all --module tasks finish."""
        src, dest = tmp_dirs
        call_order = []

        original_run_module = kape_modules.run_module

        def tracking_run_module(*args, **kwargs):
            # args[0] is module_name, args[4] is msource
            module_name = args[0]
            msource = args[4]
            call_order.append((module_name, msource))
            return original_run_module(*args, **kwargs)

        with patch("kape_modules.subprocess.run") as mock_run:
            mock_run.return_value = type("R", (), {"returncode": 0})()
            with patch("kape_modules.run_module", side_effect=tracking_run_module):
                kape_modules.main([
                    "--msource", str(src),
                    "--mdest", str(dest),
                    "--module", "MainMod",
                    "--post-process", "PostMod",
                    "--mpath", str(module_and_post_dir),
                ])

        # MainMod should have been called with src as msource
        assert call_order[0] == ("MainMod", str(src))
        # PostMod should have been called with dest as msource
        assert call_order[1] == ("PostMod", str(dest))

    def test_post_process_uses_mdest_as_source(
        self, tmp_dirs, module_and_post_dir
    ):
        """Post-process modules receive --mdest as their source directory."""
        src, dest = tmp_dirs

        with patch("kape_modules.run_module") as mock_run_module:
            kape_modules.main([
                "--msource", str(src),
                "--mdest", str(dest),
                "--module", "MainMod",
                "--post-process", "PostMod",
                "--mpath", str(module_and_post_dir),
            ])

        # run_module should have been called twice
        assert mock_run_module.call_count == 2

        # Second call (post-process) should use mdest as msource
        post_call = mock_run_module.call_args_list[1]
        post_args = post_call.args if post_call.args else ()
        # msource is arg index 4, mdest is arg index 5
        assert post_args[4] == str(dest)  # msource = mdest
        assert post_args[5] == str(dest)  # mdest = mdest

    def test_post_process_not_run_without_flag(
        self, tmp_dirs, module_and_post_dir
    ):
        """When --post-process is not specified, only --module modules run."""
        src, dest = tmp_dirs

        with patch("kape_modules.run_module") as mock_run_module:
            kape_modules.main([
                "--msource", str(src),
                "--mdest", str(dest),
                "--module", "MainMod",
                "--mpath", str(module_and_post_dir),
            ])

        # Only the main module should have been called
        assert mock_run_module.call_count == 1

    def test_post_process_multiple_modules(self, tmp_dirs, tmp_path):
        """Multiple comma-separated post-process modules all execute."""
        src, dest = tmp_dirs

        modules = tmp_path / "Modules"
        sample = modules / "Sample"
        sample.mkdir(parents=True)
        (sample / "Main.mkape").write_text(
            "Description: Main\n"
            "Category: MainCat\n"
            "Author: test\n"
            "Version: 1\n"
            "Id: 00000000-0000-0000-0000-700000000001\n"
            "ExportFormat: csv\n"
            "Processors:\n"
            "  - Executable: echo\n"
            '    CommandLine: "main"\n'
            "    ExportFormat: csv\n"
        )
        for i, name in enumerate(("PostA", "PostB"), start=2):
            (sample / f"{name}.mkape").write_text(
                f"Description: {name}\n"
                f"Category: Cat{name}\n"
                "Author: test\n"
                "Version: 1\n"
                f"Id: 00000000-0000-0000-0000-70000000000{i}\n"
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
                "--module", "Main",
                "--post-process", "PostA,PostB",
                "--mpath", str(modules),
            ])

        # Main + PostA + PostB = 3 subprocess calls
        assert mock_run.call_count == 3


# ---------------------------------------------------------------------------
# Dry-run tests
# ---------------------------------------------------------------------------


class TestPostProcessDryRun:
    """Post-process modules respect --dry-run."""

    def test_dry_run_no_subprocess(self, tmp_path):
        """Post-process with --dry-run must not call subprocess."""
        src = tmp_path / "source"
        src.mkdir()
        (src / "file.txt").write_text("data")
        dest = tmp_path / "dest"

        modules = tmp_path / "Modules"
        sample = modules / "Sample"
        sample.mkdir(parents=True)
        (sample / "DryMain.mkape").write_text(
            "Description: DryMain\n"
            "Category: MainCat\n"
            "Author: test\n"
            "Version: 1\n"
            "Id: 00000000-0000-0000-0000-800000000001\n"
            "ExportFormat: csv\n"
            "Processors:\n"
            "  - Executable: echo\n"
            '    CommandLine: "main"\n'
            "    ExportFormat: csv\n"
        )
        (sample / "DryPost.mkape").write_text(
            "Description: DryPost\n"
            "Category: PostCat\n"
            "Author: test\n"
            "Version: 1\n"
            "Id: 00000000-0000-0000-0000-800000000002\n"
            "ExportFormat: csv\n"
            "Processors:\n"
            "  - Executable: echo\n"
            '    CommandLine: "post"\n'
            "    ExportFormat: csv\n"
        )

        with patch("kape_modules.subprocess.run") as mock_run:
            kape_modules.main([
                "--msource", str(src),
                "--mdest", str(dest),
                "--module", "DryMain",
                "--post-process", "DryPost",
                "--mpath", str(modules),
                "--dry-run",
            ])
            mock_run.assert_not_called()


# ---------------------------------------------------------------------------
# Threading tests
# ---------------------------------------------------------------------------


class TestPostProcessThreading:
    """Post-process modules work correctly with --mthreads."""

    def test_post_process_with_threads(self, tmp_dirs, tmp_path):
        """Post-process modules run with threading when --mthreads > 1."""
        src, dest = tmp_dirs

        modules = tmp_path / "Modules"
        sample = modules / "Sample"
        sample.mkdir(parents=True)
        (sample / "TMain.mkape").write_text(
            "Description: TMain\n"
            "Category: MainCat\n"
            "Author: test\n"
            "Version: 1\n"
            "Id: 00000000-0000-0000-0000-900000000001\n"
            "ExportFormat: csv\n"
            "Processors:\n"
            "  - Executable: echo\n"
            '    CommandLine: "main"\n'
            "    ExportFormat: csv\n"
        )
        for i, name in enumerate(("TPostA", "TPostB"), start=2):
            (sample / f"{name}.mkape").write_text(
                f"Description: {name}\n"
                f"Category: Cat{name}\n"
                "Author: test\n"
                "Version: 1\n"
                f"Id: 00000000-0000-0000-0000-90000000000{i}\n"
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
                "--module", "TMain",
                "--post-process", "TPostA,TPostB",
                "--mpath", str(modules),
                "--mthreads", "2",
            ])

        # TMain + TPostA + TPostB = 3
        assert mock_run.call_count == 3


# ---------------------------------------------------------------------------
# Warning / edge case tests
# ---------------------------------------------------------------------------


class TestPostProcessWarnings:
    """Post-process logs warnings for missing modules."""

    def test_missing_post_process_module_warns(
        self, tmp_dirs, module_and_post_dir, caplog
    ):
        """A missing post-process module triggers a warning, not a crash."""
        src, dest = tmp_dirs

        with caplog.at_level(logging.WARNING):
            with patch("kape_modules.subprocess.run") as mock_run:
                mock_run.return_value = type("R", (), {"returncode": 0})()
                kape_modules.main([
                    "--msource", str(src),
                    "--mdest", str(dest),
                    "--module", "MainMod",
                    "--post-process", "NonExistent",
                    "--mpath", str(module_and_post_dir),
                ])

        assert any(
            "Post-process module 'NonExistent' not found" in r.message
            for r in caplog.records
        )
