"""Tests for binary subfolder support in the bin directory.

Validates that executables placed in ``bin/<stem>/<executable>``
(e.g. ``bin/pecmd/pecmd.exe``) are correctly discovered by
find_executable() and reflected in list_modules() status output.
"""

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
def modules_dir(tmp_path):
    """Create a basic Modules directory with bin/."""
    modules = tmp_path / "Modules"
    modules.mkdir()
    (modules / "bin").mkdir()
    return modules


# ---------------------------------------------------------------------------
# Tests: find_executable with bin subfolder
# ---------------------------------------------------------------------------


class TestFindExecutableBinSubfolder:
    """Tests for find_executable() subfolder lookup."""

    def test_direct_bin_takes_priority(self, modules_dir):
        """bin/<exe> should be preferred over bin/<stem>/<exe>."""
        (modules_dir / "bin" / "pecmd.exe").write_text("")
        sub = modules_dir / "bin" / "pecmd"
        sub.mkdir()
        (sub / "pecmd.exe").write_text("")

        result = kape_modules.find_executable("pecmd.exe", modules_dir, "SomeModule")
        assert result == str(modules_dir / "bin" / "pecmd.exe")

    def test_subfolder_found_when_no_direct(self, modules_dir):
        """bin/<stem>/<exe> should be found when bin/<exe> does not exist."""
        sub = modules_dir / "bin" / "pecmd"
        sub.mkdir()
        (sub / "pecmd.exe").write_text("")

        result = kape_modules.find_executable("pecmd.exe", modules_dir, "SomeModule")
        assert result == str(modules_dir / "bin" / "pecmd" / "pecmd.exe")

    def test_module_named_dir_takes_priority(self, modules_dir):
        """<modules_dir>/<module_name>/<exe> should still be highest priority."""
        mod_dir = modules_dir / "MyModule"
        mod_dir.mkdir()
        (mod_dir / "pecmd.exe").write_text("")
        sub = modules_dir / "bin" / "pecmd"
        sub.mkdir()
        (sub / "pecmd.exe").write_text("")

        result = kape_modules.find_executable("pecmd.exe", modules_dir, "MyModule")
        assert result == str(modules_dir / "MyModule" / "pecmd.exe")

    def test_fallback_when_nothing_found(self, modules_dir):
        """Falls back to bare name when exe is not in any known location."""
        result = kape_modules.find_executable("missing.exe", modules_dir, "SomeModule")
        assert result == "missing.exe"

    def test_subfolder_case_sensitive_stem(self, modules_dir):
        """Subfolder lookup uses the stem of the executable filename."""
        sub = modules_dir / "bin" / "LECmd"
        sub.mkdir()
        (sub / "LECmd.exe").write_text("")

        result = kape_modules.find_executable("LECmd.exe", modules_dir, "SomeModule")
        assert result == str(modules_dir / "bin" / "LECmd" / "LECmd.exe")


# ---------------------------------------------------------------------------
# Tests: platform mapping with bin subfolder
# ---------------------------------------------------------------------------


class TestPlatformMappingWithSubfolder:
    """Platform mapping DLL resolution should work when exe is in a subfolder."""

    @pytest.fixture
    def platform_map_data(self):
        return {
            "executables": {
                "pecmd.exe": {
                    "linux": {
                        "executable": "dotnet",
                        "command_line": "pecmd.dll {original_args}",
                    },
                },
            },
        }

    def test_dll_resolved_against_subfolder(self, modules_dir, platform_map_data):
        """When exe is in bin/<stem>/<exe>, the DLL should resolve to
        bin/<stem>/<dll>."""
        sub = modules_dir / "bin" / "pecmd"
        sub.mkdir()
        (sub / "pecmd.exe").write_text("")

        exe = kape_modules.find_executable("pecmd.exe", modules_dir, "SomeModule")
        exe, cmd = kape_modules.apply_platform_mapping(
            exe, "-d /src --csv /dst", platform_map_data,
            current_platform="linux",
        )

        assert exe == "dotnet"
        expected_dll = str(modules_dir / "bin" / "pecmd" / "pecmd.dll")
        assert expected_dll in cmd
        assert cmd.endswith("-d /src --csv /dst")


# ---------------------------------------------------------------------------
# Tests: run_module integration with bin subfolder
# ---------------------------------------------------------------------------


class TestRunModuleWithSubfolder:
    """Integration: run_module should use executables from bin subfolders."""

    @pytest.fixture
    def sample_module_dir_subfolder(self, tmp_path):
        """Modules dir with executable only in bin/<stem>/<exe>."""
        modules = tmp_path / "Modules"
        sample = modules / "Sample"
        sample.mkdir(parents=True)
        sub = modules / "bin" / "pecmd"
        sub.mkdir(parents=True)
        (sub / "pecmd.exe").write_text("")
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

    @patch("subprocess.run")
    def test_run_module_finds_exe_in_subfolder(
        self, mock_run, tmp_path, sample_module_dir_subfolder,
    ):
        """run_module should find and use executable from bin/<stem>/<exe>."""
        src = tmp_path / "source"
        src.mkdir()
        dest = tmp_path / "dest"

        modules = sample_module_dir_subfolder
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
        expected_exe = str(modules / "bin" / "pecmd" / "pecmd.exe")
        assert expected_exe in cmd_str

    @patch("kape_modules.CURRENT_PLATFORM", "linux")
    @patch("subprocess.run")
    def test_run_module_platform_map_with_subfolder(
        self, mock_run, tmp_path, sample_module_dir_subfolder,
    ):
        """Platform mapping should work correctly when exe is in a subfolder."""
        src = tmp_path / "source"
        src.mkdir()
        dest = tmp_path / "dest"

        modules = sample_module_dir_subfolder
        module_file = modules / "Sample" / "PECmd.mkape"
        module_data = kape_modules.load_module(module_file)

        platform_map = {
            "executables": {
                "pecmd.exe": {
                    "linux": {
                        "executable": "dotnet",
                        "command_line": "pecmd.dll {original_args}",
                    },
                },
            },
        }

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
            platform_map=platform_map,
        )

        assert mock_run.called
        cmd_str = mock_run.call_args[0][0]
        assert "dotnet" in cmd_str
        expected_dll = str(modules / "bin" / "pecmd" / "pecmd.dll")
        assert expected_dll in cmd_str
