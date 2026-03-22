"""Tests for the --msync feature."""

import io
import logging
import zipfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import kape_modules


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_zip(file_map: dict) -> bytes:
    """Build an in-memory zip archive from a dict of {path: content_str}."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in file_map.items():
            zf.writestr(name, content)
    return buf.getvalue()


def _make_mock_urlopen(zip_bytes: bytes):
    """Return a context-manager mock that yields *zip_bytes* from urlopen."""
    resp = MagicMock()
    resp.read.return_value = zip_bytes
    resp.__enter__ = MagicMock(return_value=resp)
    resp.__exit__ = MagicMock(return_value=False)
    return resp


# ---------------------------------------------------------------------------
# Unit tests for helpers
# ---------------------------------------------------------------------------


class TestSha1Helpers:
    def test_sha1_bytes_deterministic(self):
        assert kape_modules._sha1_bytes(b"hello") == kape_modules._sha1_bytes(b"hello")

    def test_sha1_file_matches_bytes(self, tmp_path):
        p = tmp_path / "f.txt"
        p.write_bytes(b"world")
        assert kape_modules._sha1(p) == kape_modules._sha1_bytes(b"world")


class TestIsPreservedDir:
    @pytest.mark.parametrize("rel", [
        Path("!Disabled/SomeMod.mkape"),
        Path("!Local/MyMod.mkape"),
        Path("bin/tool.exe"),
        Path("Sample/Test.mkape"),
    ])
    def test_preserved(self, rel):
        assert kape_modules._is_preserved_dir(rel) is True

    @pytest.mark.parametrize("rel", [
        Path("Apps/SomeMod.mkape"),
        Path("EZTools/Tool.mkape"),
        Path("Compound/All.mkape"),
    ])
    def test_not_preserved(self, rel):
        assert kape_modules._is_preserved_dir(rel) is False


# ---------------------------------------------------------------------------
# sync_modules tests
# ---------------------------------------------------------------------------


class TestSyncModules:
    """Tests for the sync_modules function."""

    def _zip_with_modules(self, files: dict) -> bytes:
        """Build a zip with a KapeFiles-master/Modules/ prefix."""
        prefixed = {}
        for name, content in files.items():
            prefixed[f"KapeFiles-master/Modules/{name}"] = content
        return _build_zip(prefixed)

    def test_adds_new_modules(self, tmp_path):
        """New .mkape files should be created in the local Modules dir."""
        modules_dir = tmp_path / "Modules"
        modules_dir.mkdir()

        zip_bytes = self._zip_with_modules({
            "Apps/NewMod.mkape": "Description: New module\n",
        })

        mock_resp = _make_mock_urlopen(zip_bytes)
        with patch("kape_modules.urllib.request.urlopen", return_value=mock_resp):
            kape_modules.sync_modules(modules_dir)

        assert (modules_dir / "Apps" / "NewMod.mkape").exists()
        assert (modules_dir / "Apps" / "NewMod.mkape").read_text() == "Description: New module\n"

    def test_updates_changed_modules(self, tmp_path):
        """Modules with different SHA-1 should be overwritten."""
        modules_dir = tmp_path / "Modules"
        apps = modules_dir / "Apps"
        apps.mkdir(parents=True)
        (apps / "Mod.mkape").write_text("old content")

        zip_bytes = self._zip_with_modules({
            "Apps/Mod.mkape": "new content",
        })

        mock_resp = _make_mock_urlopen(zip_bytes)
        with patch("kape_modules.urllib.request.urlopen", return_value=mock_resp):
            kape_modules.sync_modules(modules_dir)

        assert (apps / "Mod.mkape").read_text() == "new content"

    def test_skips_identical_modules(self, tmp_path):
        """Modules with identical SHA-1 should not be rewritten."""
        modules_dir = tmp_path / "Modules"
        apps = modules_dir / "Apps"
        apps.mkdir(parents=True)
        content = "Description: Same module\n"
        target = apps / "Same.mkape"
        target.write_text(content)
        mtime_before = target.stat().st_mtime_ns

        zip_bytes = self._zip_with_modules({
            "Apps/Same.mkape": content,
        })

        mock_resp = _make_mock_urlopen(zip_bytes)
        with patch("kape_modules.urllib.request.urlopen", return_value=mock_resp):
            kape_modules.sync_modules(modules_dir)

        # File should not have been touched
        assert target.stat().st_mtime_ns == mtime_before

    def test_moves_local_only_to_local(self, tmp_path):
        """Modules not in the remote repo should be moved to !Local."""
        modules_dir = tmp_path / "Modules"
        custom_dir = modules_dir / "Custom"
        custom_dir.mkdir(parents=True)
        (custom_dir / "MyMod.mkape").write_text("my custom module")

        # Remote has no modules
        zip_bytes = self._zip_with_modules({
            "Apps/Official.mkape": "official module",
        })

        mock_resp = _make_mock_urlopen(zip_bytes)
        with patch("kape_modules.urllib.request.urlopen", return_value=mock_resp):
            kape_modules.sync_modules(modules_dir)

        # Custom module should be moved to !Local
        assert (modules_dir / "!Local" / "MyMod.mkape").exists()
        assert (modules_dir / "!Local" / "MyMod.mkape").read_text() == "my custom module"
        # Original should be gone
        assert not (custom_dir / "MyMod.mkape").exists()

    def test_preserves_disabled_dir(self, tmp_path):
        """Modules in !Disabled should not be touched."""
        modules_dir = tmp_path / "Modules"
        disabled_dir = modules_dir / "!Disabled"
        disabled_dir.mkdir(parents=True)
        (disabled_dir / "Old.mkape").write_text("disabled module")

        zip_bytes = self._zip_with_modules({
            "Apps/New.mkape": "new module",
        })

        mock_resp = _make_mock_urlopen(zip_bytes)
        with patch("kape_modules.urllib.request.urlopen", return_value=mock_resp):
            kape_modules.sync_modules(modules_dir)

        # Disabled module should remain untouched
        assert (disabled_dir / "Old.mkape").read_text() == "disabled module"

    def test_preserves_local_dir(self, tmp_path):
        """Modules already in !Local should not be touched."""
        modules_dir = tmp_path / "Modules"
        local_dir = modules_dir / "!Local"
        local_dir.mkdir(parents=True)
        (local_dir / "Mine.mkape").write_text("my local module")

        zip_bytes = self._zip_with_modules({
            "Apps/New.mkape": "new module",
        })

        mock_resp = _make_mock_urlopen(zip_bytes)
        with patch("kape_modules.urllib.request.urlopen", return_value=mock_resp):
            kape_modules.sync_modules(modules_dir)

        assert (local_dir / "Mine.mkape").read_text() == "my local module"

    def test_preserves_bin_dir(self, tmp_path):
        """Files in the bin directory should not be touched."""
        modules_dir = tmp_path / "Modules"
        bin_dir = modules_dir / "bin"
        bin_dir.mkdir(parents=True)
        (bin_dir / "tool.exe").write_bytes(b"\x00binary")

        zip_bytes = self._zip_with_modules({
            "Apps/New.mkape": "new module",
        })

        mock_resp = _make_mock_urlopen(zip_bytes)
        with patch("kape_modules.urllib.request.urlopen", return_value=mock_resp):
            kape_modules.sync_modules(modules_dir)

        assert (bin_dir / "tool.exe").read_bytes() == b"\x00binary"

    def test_creates_modules_dir_if_missing(self, tmp_path):
        """sync_modules should create the Modules directory if it doesn't exist."""
        modules_dir = tmp_path / "Modules"
        assert not modules_dir.exists()

        zip_bytes = self._zip_with_modules({
            "Apps/New.mkape": "new module",
        })

        mock_resp = _make_mock_urlopen(zip_bytes)
        with patch("kape_modules.urllib.request.urlopen", return_value=mock_resp):
            kape_modules.sync_modules(modules_dir)

        assert modules_dir.exists()
        assert (modules_dir / "Apps" / "New.mkape").exists()

    def test_download_failure_logs_error(self, tmp_path, caplog):
        """A download failure should log an error and return gracefully."""
        modules_dir = tmp_path / "Modules"
        modules_dir.mkdir()

        with patch("kape_modules.urllib.request.urlopen", side_effect=Exception("network error")):
            with caplog.at_level(logging.ERROR):
                kape_modules.sync_modules(modules_dir)

        assert any("Failed to download" in r.message for r in caplog.records)

    def test_invalid_zip_logs_error(self, tmp_path, caplog):
        """A corrupted zip should log an error and return gracefully."""
        modules_dir = tmp_path / "Modules"
        modules_dir.mkdir()

        mock_resp = _make_mock_urlopen(b"not a zip file")
        with patch("kape_modules.urllib.request.urlopen", return_value=mock_resp):
            with caplog.at_level(logging.ERROR):
                kape_modules.sync_modules(modules_dir)

        assert any("not a valid zip" in r.message for r in caplog.records)

    def test_custom_url(self, tmp_path):
        """A custom URL should be passed to urlopen."""
        modules_dir = tmp_path / "Modules"
        modules_dir.mkdir()
        custom_url = "https://example.com/my-fork/archive/main.zip"

        zip_bytes = self._zip_with_modules({
            "Apps/Mod.mkape": "content",
        })
        mock_resp = _make_mock_urlopen(zip_bytes)
        with patch("kape_modules.urllib.request.urlopen", return_value=mock_resp) as mock_open:
            kape_modules.sync_modules(modules_dir, url=custom_url)

        # Verify the custom URL was used in the request
        call_args = mock_open.call_args
        request_obj = call_args[0][0]
        assert request_obj.full_url == custom_url

    def test_logs_summary(self, tmp_path, caplog):
        """Sync should log a summary of new/updated/moved counts."""
        modules_dir = tmp_path / "Modules"
        modules_dir.mkdir()

        zip_bytes = self._zip_with_modules({
            "Apps/New.mkape": "new module",
        })

        mock_resp = _make_mock_urlopen(zip_bytes)
        with patch("kape_modules.urllib.request.urlopen", return_value=mock_resp):
            with caplog.at_level(logging.INFO):
                kape_modules.sync_modules(modules_dir)

        assert any("Sync complete" in r.message for r in caplog.records)
        assert any("1 new" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# CLI integration tests
# ---------------------------------------------------------------------------


class TestMainMsync:
    """End-to-end: main() with --msync should call sync_modules and exit."""

    def test_msync_calls_sync(self, tmp_path):
        """--msync should trigger sync_modules."""
        with patch("kape_modules.sync_modules") as mock_sync:
            kape_modules.main([
                "--msync",
                "--mpath", str(tmp_path / "Modules"),
            ])
            mock_sync.assert_called_once()

    def test_msync_with_custom_url(self, tmp_path):
        """--msync URL should pass the URL to sync_modules."""
        custom_url = "https://example.com/fork/archive/main.zip"
        with patch("kape_modules.sync_modules") as mock_sync:
            kape_modules.main([
                "--msync", custom_url,
                "--mpath", str(tmp_path / "Modules"),
            ])
            mock_sync.assert_called_once()
            call_args = mock_sync.call_args
            assert call_args[0][1] == custom_url

    def test_msync_default_url(self, tmp_path):
        """--msync without URL should use the default KapeFiles URL."""
        with patch("kape_modules.sync_modules") as mock_sync:
            kape_modules.main([
                "--msync",
                "--mpath", str(tmp_path / "Modules"),
            ])
            call_args = mock_sync.call_args
            assert call_args[0][1] == kape_modules.KAPEFILES_DEFAULT_URL

    def test_msync_exits_without_running_modules(self, tmp_path):
        """--msync should return without requiring --msource/--mdest/--module."""
        with patch("kape_modules.sync_modules"):
            # This should NOT raise an error about missing arguments
            kape_modules.main([
                "--msync",
                "--mpath", str(tmp_path / "Modules"),
            ])
