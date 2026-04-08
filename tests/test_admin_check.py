"""Tests for admin privilege checking."""

import logging
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock
import io

import pytest

# Ensure the parent package is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import kape_modules


# ---------------------------------------------------------------------------
# Tests for _check_admin_privileges
# ---------------------------------------------------------------------------


class TestCheckAdminPrivileges:
    """Test the _check_admin_privileges function."""

    @patch("sys.platform", "win32")
    def test_windows_is_admin(self):
        """On Windows, return True when IsUserAnAdmin returns non-zero."""
        # Create a mock ctypes module
        mock_ctypes = MagicMock()
        mock_ctypes.windll.shell32.IsUserAnAdmin.return_value = 1

        with patch.dict("sys.modules", {"ctypes": mock_ctypes}):
            result = kape_modules._check_admin_privileges()
            assert result is True

    @patch("sys.platform", "win32")
    def test_windows_not_admin(self):
        """On Windows, return False when IsUserAnAdmin returns 0."""
        # Create a mock ctypes module
        mock_ctypes = MagicMock()
        mock_ctypes.windll.shell32.IsUserAnAdmin.return_value = 0

        with patch.dict("sys.modules", {"ctypes": mock_ctypes}):
            result = kape_modules._check_admin_privileges()
            assert result is False

    @patch("sys.platform", "win32")
    def test_windows_exception(self):
        """On Windows, return False when ctypes raises an exception."""
        # Create a mock ctypes module that raises exception
        mock_ctypes = MagicMock()
        mock_ctypes.windll.shell32.IsUserAnAdmin.side_effect = Exception("Error")

        with patch.dict("sys.modules", {"ctypes": mock_ctypes}):
            result = kape_modules._check_admin_privileges()
            assert result is False

    @patch("sys.platform", "linux")
    def test_unix_is_root(self):
        """On Unix, return True when euid is 0."""
        with patch("kape_modules.os.geteuid", return_value=0):
            result = kape_modules._check_admin_privileges()
            assert result is True

    @patch("sys.platform", "linux")
    def test_unix_not_root(self):
        """On Unix, return False when euid is non-zero."""
        with patch("kape_modules.os.geteuid", return_value=1000):
            result = kape_modules._check_admin_privileges()
            assert result is False

    @patch("sys.platform", "linux")
    def test_unix_no_geteuid(self):
        """On Unix systems without geteuid, return False."""
        with patch.object(kape_modules.os, "geteuid", side_effect=AttributeError):
            result = kape_modules._check_admin_privileges()
            assert result is False


# ---------------------------------------------------------------------------
# Tests for check_and_warn_admin_privileges
# ---------------------------------------------------------------------------


class TestCheckAndWarnAdminPrivileges:
    """Test the check_and_warn_admin_privileges function."""

    def test_warning_when_not_admin(self, caplog):
        """When not admin, print warning to stderr and log warning."""
        with patch("kape_modules._check_admin_privileges", return_value=False):
            stderr_capture = io.StringIO()
            with patch("sys.stderr", stderr_capture):
                with caplog.at_level(logging.WARNING):
                    kape_modules.check_and_warn_admin_privileges()

            # Check stderr output
            stderr_output = stderr_capture.getvalue()
            assert "WARNING" in stderr_output
            assert "Not running with administrative privileges" in stderr_output

            # Check log output
            assert any(
                "Not running with administrative privileges" in r.message
                for r in caplog.records
            )

    def test_no_warning_when_admin(self, caplog):
        """When admin, do not print or log warning."""
        with patch("kape_modules._check_admin_privileges", return_value=True):
            stderr_capture = io.StringIO()
            with patch("sys.stderr", stderr_capture):
                with caplog.at_level(logging.WARNING):
                    kape_modules.check_and_warn_admin_privileges()

            # Check no stderr output
            stderr_output = stderr_capture.getvalue()
            assert stderr_output == ""

            # Check no log output
            assert not any(
                "Not running with administrative privileges" in r.message
                for r in caplog.records
            )


# ---------------------------------------------------------------------------
# Integration test: main() calls admin check
# ---------------------------------------------------------------------------


class TestMainAdminCheck:
    """Test that main() calls the admin privilege check."""

    def test_main_calls_admin_check(self, tmp_path):
        """main() should call check_and_warn_admin_privileges early in execution."""
        # Create minimal setup for main() to run
        src = tmp_path / "source"
        src.mkdir()
        dest = tmp_path / "output"

        modules = tmp_path / "Modules"
        sample = modules / "Sample"
        sample.mkdir(parents=True)
        (sample / "TestMod.mkape").write_text(
            "Description: Test\n"
            "Category: Test\n"
            "Author: test\n"
            "Version: 1\n"
            "Id: 00000000-0000-0000-0000-000000000001\n"
            "ExportFormat: csv\n"
            "Processors:\n"
            "  - Executable: echo\n"
            '    CommandLine: "test"\n'
            "    ExportFormat: csv\n"
        )

        with patch("kape_modules.check_and_warn_admin_privileges") as mock_check:
            with patch("kape_modules.subprocess.run"):
                kape_modules.main([
                    "--msource", str(src),
                    "--mdest", str(dest),
                    "--module", "TestMod",
                    "--mpath", str(modules),
                ])
                # Verify the admin check was called
                mock_check.assert_called_once()

    def test_main_list_calls_admin_check(self, tmp_path):
        """main() with --mlist should also call admin check."""
        modules = tmp_path / "Modules"
        modules.mkdir()

        with patch("kape_modules.check_and_warn_admin_privileges") as mock_check:
            with patch("kape_modules.list_modules"):
                kape_modules.main([
                    "--mlist",
                    "--mpath", str(modules),
                ])
                # Verify the admin check was called
                mock_check.assert_called_once()

    def test_main_sync_calls_admin_check(self, tmp_path):
        """main() with --msync should also call admin check."""
        modules = tmp_path / "Modules"
        modules.mkdir()

        with patch("kape_modules.check_and_warn_admin_privileges") as mock_check:
            with patch("kape_modules.sync_modules"):
                kape_modules.main([
                    "--msync",
                    "--mpath", str(modules),
                ])
                # Verify the admin check was called
                mock_check.assert_called_once()
