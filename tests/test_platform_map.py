"""Tests for the cross-platform executable mapping feature."""

import logging
import os
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
def platform_map_file(tmp_path):
    """Create a sample platform_map.yaml file."""
    pmap = tmp_path / "platform_map.yaml"
    pmap.write_text(
        "executables:\n"
        "  pecmd.exe:\n"
        "    linux:\n"
        "      executable: dotnet\n"
        '      command_line: "pecmd.dll {original_args}"\n'
        "    darwin:\n"
        "      executable: dotnet\n"
        '      command_line: "pecmd.dll {original_args}"\n'
        "  evtxecmd.exe:\n"
        "    linux:\n"
        "      executable: dotnet\n"
        '      command_line: "EvtxECmd.dll {original_args}"\n'
        "  noargs.exe:\n"
        "    linux:\n"
        "      executable: noargs-linux\n"
    )
    return pmap


@pytest.fixture
def platform_map_data():
    """Return a loaded platform map dict for use in unit tests."""
    return {
        "executables": {
            "pecmd.exe": {
                "linux": {
                    "executable": "dotnet",
                    "command_line": "pecmd.dll {original_args}",
                },
                "darwin": {
                    "executable": "dotnet",
                    "command_line": "pecmd.dll {original_args}",
                },
            },
            "EvtxECmd.exe": {
                "linux": {
                    "executable": "dotnet",
                    "command_line": "EvtxECmd.dll {original_args}",
                },
            },
            "noargs.exe": {
                "linux": {
                    "executable": "noargs-linux",
                },
            },
        },
    }


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
    """Create a Modules directory with a module using a Windows executable."""
    modules = tmp_path / "Modules"
    sample = modules / "Sample"
    sample.mkdir(parents=True)
    mkape = sample / "PECmd.mkape"
    mkape.write_text(
        "Description: Prefetch module\n"
        "Category: FileSystem\n"
        "Author: test\n"
        "Version: 1\n"
        "Id: 00000000-0000-0000-0000-000000000099\n"
        "ExportFormat: csv\n"
        "Processors:\n"
        "  - Executable: pecmd.exe\n"
        '    CommandLine: "-d %sourceDirectory% --csv %destinationDirectory%"\n'
        "    ExportFormat: csv\n"
        "    ExportFile: prefetch.csv\n"
    )
    return modules


# ---------------------------------------------------------------------------
# Tests: load_platform_map
# ---------------------------------------------------------------------------


class TestLoadPlatformMap:
    """Tests for the load_platform_map function."""

    def test_returns_empty_when_none(self):
        result = kape_modules.load_platform_map(None)
        assert result == {}

    def test_returns_empty_when_file_missing(self, tmp_path):
        result = kape_modules.load_platform_map(tmp_path / "nonexistent.yaml")
        assert result == {}

    def test_loads_valid_file(self, platform_map_file):
        result = kape_modules.load_platform_map(platform_map_file)
        assert "executables" in result
        assert "pecmd.exe" in result["executables"]

    def test_returns_empty_on_invalid_yaml(self, tmp_path):
        bad = tmp_path / "bad.yaml"
        bad.write_text(": : : invalid\n  not: valid:\n")
        result = kape_modules.load_platform_map(bad)
        # Should not raise, returns empty dict on error
        assert isinstance(result, dict)

    def test_returns_empty_on_empty_file(self, tmp_path):
        empty = tmp_path / "empty.yaml"
        empty.write_text("")
        result = kape_modules.load_platform_map(empty)
        assert result == {}


# ---------------------------------------------------------------------------
# Tests: apply_platform_mapping
# ---------------------------------------------------------------------------


class TestApplyPlatformMapping:
    """Tests for the apply_platform_mapping function."""

    def test_no_mapping_returns_original(self):
        exe, cmd = kape_modules.apply_platform_mapping(
            "pecmd.exe", "-d /src --csv /dst", {}, current_platform="linux"
        )
        assert exe == "pecmd.exe"
        assert cmd == "-d /src --csv /dst"

    def test_windows_platform_skips_mapping(self, platform_map_data):
        exe, cmd = kape_modules.apply_platform_mapping(
            "pecmd.exe", "-d /src", platform_map_data, current_platform="windows"
        )
        assert exe == "pecmd.exe"
        assert cmd == "-d /src"

    def test_linux_mapping_replaces_executable(self, platform_map_data):
        exe, cmd = kape_modules.apply_platform_mapping(
            "pecmd.exe", "-d /src --csv /dst", platform_map_data,
            current_platform="linux",
        )
        assert exe == "dotnet"
        assert cmd == "pecmd.dll -d /src --csv /dst"

    def test_darwin_mapping_replaces_executable(self, platform_map_data):
        exe, cmd = kape_modules.apply_platform_mapping(
            "pecmd.exe", "-d /src --csv /dst", platform_map_data,
            current_platform="darwin",
        )
        assert exe == "dotnet"
        assert cmd == "pecmd.dll -d /src --csv /dst"

    def test_case_insensitive_lookup(self, platform_map_data):
        """Executable lookup should be case-insensitive."""
        exe, cmd = kape_modules.apply_platform_mapping(
            "PECmd.exe", "-d /src", platform_map_data, current_platform="linux"
        )
        assert exe == "dotnet"

    def test_no_command_line_preserves_original_args(self, platform_map_data):
        """When command_line is not in the mapping, original cmdline is kept."""
        exe, cmd = kape_modules.apply_platform_mapping(
            "noargs.exe", "--flag value", platform_map_data,
            current_platform="linux",
        )
        assert exe == "noargs-linux"
        assert cmd == "--flag value"

    def test_unmapped_executable_unchanged(self, platform_map_data):
        exe, cmd = kape_modules.apply_platform_mapping(
            "unknown.exe", "--flag", platform_map_data, current_platform="linux"
        )
        assert exe == "unknown.exe"
        assert cmd == "--flag"

    def test_unmapped_platform_unchanged(self, platform_map_data):
        """EvtxECmd.exe has linux but not darwin mapping."""
        exe, cmd = kape_modules.apply_platform_mapping(
            "EvtxECmd.exe", "--flag", platform_map_data, current_platform="darwin"
        )
        assert exe == "EvtxECmd.exe"
        assert cmd == "--flag"

    def test_original_args_placeholder_replaced(self, platform_map_data):
        """The {original_args} placeholder in command_line should be replaced."""
        exe, cmd = kape_modules.apply_platform_mapping(
            "pecmd.exe", "-d /evidence --csv /out", platform_map_data,
            current_platform="linux",
        )
        assert cmd == "pecmd.dll -d /evidence --csv /out"


# ---------------------------------------------------------------------------
# Tests: CURRENT_PLATFORM constant
# ---------------------------------------------------------------------------


class TestCurrentPlatform:
    """Tests that CURRENT_PLATFORM is set to a valid value."""

    def test_platform_is_string(self):
        assert isinstance(kape_modules.CURRENT_PLATFORM, str)

    def test_platform_in_expected_values(self):
        assert kape_modules.CURRENT_PLATFORM in ("windows", "linux", "darwin")


# ---------------------------------------------------------------------------
# Tests: --platform-map CLI argument
# ---------------------------------------------------------------------------


class TestPlatformMapArgument:
    """Tests for the --platform-map CLI argument."""

    def test_default_is_none(self):
        parser = kape_modules.build_argument_parser()
        args = parser.parse_args([])
        assert args.platform_map is None

    def test_custom_path(self):
        parser = kape_modules.build_argument_parser()
        args = parser.parse_args(["--platform-map", "/my/map.yaml"])
        assert args.platform_map == "/my/map.yaml"


# ---------------------------------------------------------------------------
# Tests: integration — run_module with platform mapping
# ---------------------------------------------------------------------------


class TestRunModuleWithPlatformMapping:
    """Integration tests: run_module applies platform mapping."""

    @patch("kape_modules.CURRENT_PLATFORM", "linux")
    @patch("subprocess.run")
    def test_mapping_applied_in_run_module(
        self, mock_run, tmp_dirs, sample_module_dir, platform_map_data,
    ):
        """run_module should replace the executable via platform mapping."""
        src, dest = tmp_dirs
        modules = sample_module_dir

        module_file = modules / "Sample" / "PECmd.mkape"
        module_data = kape_modules.load_module(module_file)

        kape_modules.run_module(
            "PECmd",
            module_data,
            module_file,
            modules,
            str(src),
            str(dest),
            None,
            {},
            False,
            set(),
            dry_run=False,
            num_threads=1,
            platform_map=platform_map_data,
        )

        assert mock_run.called
        cmd_str = mock_run.call_args[0][0]
        assert "dotnet" in cmd_str
        assert "pecmd.dll" in cmd_str

    @patch("kape_modules.CURRENT_PLATFORM", "windows")
    @patch("subprocess.run")
    def test_no_mapping_on_windows(
        self, mock_run, tmp_dirs, sample_module_dir, platform_map_data,
    ):
        """On Windows, no mapping should be applied."""
        src, dest = tmp_dirs
        modules = sample_module_dir

        module_file = modules / "Sample" / "PECmd.mkape"
        module_data = kape_modules.load_module(module_file)

        kape_modules.run_module(
            "PECmd",
            module_data,
            module_file,
            modules,
            str(src),
            str(dest),
            None,
            {},
            False,
            set(),
            dry_run=False,
            num_threads=1,
            platform_map=platform_map_data,
        )

        assert mock_run.called
        cmd_str = mock_run.call_args[0][0]
        assert "pecmd.exe" in cmd_str
        assert "dotnet" not in cmd_str

    @patch("kape_modules.CURRENT_PLATFORM", "linux")
    @patch("subprocess.run")
    def test_no_platform_map_leaves_executable_unchanged(
        self, mock_run, tmp_dirs, sample_module_dir,
    ):
        """Without a platform map, no mapping is applied."""
        src, dest = tmp_dirs
        modules = sample_module_dir

        module_file = modules / "Sample" / "PECmd.mkape"
        module_data = kape_modules.load_module(module_file)

        kape_modules.run_module(
            "PECmd",
            module_data,
            module_file,
            modules,
            str(src),
            str(dest),
            None,
            {},
            False,
            set(),
            dry_run=False,
            num_threads=1,
            platform_map=None,
        )

        assert mock_run.called
        cmd_str = mock_run.call_args[0][0]
        assert "pecmd.exe" in cmd_str


# ---------------------------------------------------------------------------
# Tests: integration — main() with --platform-map
# ---------------------------------------------------------------------------


class TestMainPlatformMap:
    """Integration tests: main() loads and uses platform map."""

    @patch("kape_modules.CURRENT_PLATFORM", "linux")
    @patch("subprocess.run")
    def test_main_with_platform_map(
        self, mock_run, tmp_dirs, sample_module_dir, platform_map_file,
    ):
        """main() should load the platform map and apply it."""
        src, dest = tmp_dirs
        modules = sample_module_dir

        kape_modules.main([
            "--msource", str(src),
            "--mdest", str(dest),
            "--module", "PECmd",
            "--mpath", str(modules),
            "--platform-map", str(platform_map_file),
        ])

        assert mock_run.called
        cmd_str = mock_run.call_args[0][0]
        assert "dotnet" in cmd_str
        assert "pecmd.dll" in cmd_str

    @patch("subprocess.run")
    def test_main_without_platform_map(
        self, mock_run, tmp_dirs, sample_module_dir,
    ):
        """main() without --platform-map should still work (no mapping)."""
        src, dest = tmp_dirs
        modules = sample_module_dir

        kape_modules.main([
            "--msource", str(src),
            "--mdest", str(dest),
            "--module", "PECmd",
            "--mpath", str(modules),
            "--platform-map", "/nonexistent/path.yaml",
        ])

        assert mock_run.called
        cmd_str = mock_run.call_args[0][0]
        # Should use original executable since map file doesn't exist
        assert "pecmd.exe" in cmd_str
