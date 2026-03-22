"""Tests for the --msync feature."""

import io
import logging
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest

import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import kape_modules


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_fake_zip(files: dict[str, bytes]) -> bytes:
    """Create an in-memory ZIP archive from a dict of {path: content}."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return buf.getvalue()


class _FakeResponse:
    """Minimal file-like object returned by a fake urlopen."""

    def __init__(self, data: bytes):
        self._data = data

    def read(self) -> bytes:
        return self._data


# ---------------------------------------------------------------------------
# sync_modules unit tests
# ---------------------------------------------------------------------------


class TestSyncModules:
    """sync_modules should download, extract and update the Modules dir."""

    def test_adds_new_files(self, tmp_path):
        """New .mkape files from the archive are written to the local dir."""
        modules_dir = tmp_path / "Modules"
        modules_dir.mkdir()

        zip_bytes = _build_fake_zip({
            "KapeFiles-master/Modules/Cat/Example.mkape": b"Description: ex\n",
        })
        fake_open = lambda url: _FakeResponse(zip_bytes)

        kape_modules.sync_modules(modules_dir, _urlopen=fake_open)

        dest = modules_dir / "Cat" / "Example.mkape"
        assert dest.exists()
        assert dest.read_bytes() == b"Description: ex\n"

    def test_updates_changed_files(self, tmp_path):
        """Changed files are overwritten."""
        modules_dir = tmp_path / "Modules"
        cat = modules_dir / "Cat"
        cat.mkdir(parents=True)
        existing = cat / "Example.mkape"
        existing.write_bytes(b"old content")

        zip_bytes = _build_fake_zip({
            "KapeFiles-master/Modules/Cat/Example.mkape": b"new content",
        })
        fake_open = lambda url: _FakeResponse(zip_bytes)

        kape_modules.sync_modules(modules_dir, _urlopen=fake_open)

        assert existing.read_bytes() == b"new content"

    def test_unchanged_files_not_rewritten(self, tmp_path):
        """Files with identical content are left untouched."""
        modules_dir = tmp_path / "Modules"
        cat = modules_dir / "Cat"
        cat.mkdir(parents=True)
        existing = cat / "Example.mkape"
        existing.write_bytes(b"same")

        zip_bytes = _build_fake_zip({
            "KapeFiles-master/Modules/Cat/Example.mkape": b"same",
        })
        fake_open = lambda url: _FakeResponse(zip_bytes)

        kape_modules.sync_modules(modules_dir, _urlopen=fake_open)

        assert existing.read_bytes() == b"same"

    def test_logs_summary(self, tmp_path, caplog):
        """Sync logs an add/update/unchanged summary."""
        modules_dir = tmp_path / "Modules"
        cat = modules_dir / "Cat"
        cat.mkdir(parents=True)
        (cat / "Old.mkape").write_bytes(b"old content")
        (cat / "Same.mkape").write_bytes(b"same content")

        zip_bytes = _build_fake_zip({
            "KapeFiles-master/Modules/Cat/Old.mkape": b"new content",
            "KapeFiles-master/Modules/Cat/Same.mkape": b"same content",
            "KapeFiles-master/Modules/Cat/Brand.mkape": b"brand new",
        })
        fake_open = lambda url: _FakeResponse(zip_bytes)

        with caplog.at_level(logging.INFO):
            kape_modules.sync_modules(modules_dir, _urlopen=fake_open)

        summary = [r.message for r in caplog.records if "Sync complete" in r.message]
        assert len(summary) == 1
        assert "1 added" in summary[0]
        assert "1 updated" in summary[0]
        assert "1 unchanged" in summary[0]

    def test_ignores_non_module_entries(self, tmp_path):
        """Files outside the Modules/ prefix are ignored."""
        modules_dir = tmp_path / "Modules"
        modules_dir.mkdir()

        zip_bytes = _build_fake_zip({
            "KapeFiles-master/README.md": b"# readme",
            "KapeFiles-master/Targets/T.tkape": b"target",
            "KapeFiles-master/Modules/A/Test.mkape": b"data",
        })
        fake_open = lambda url: _FakeResponse(zip_bytes)

        kape_modules.sync_modules(modules_dir, _urlopen=fake_open)

        # Only the Modules entry should exist
        assert (modules_dir / "A" / "Test.mkape").exists()
        assert not (modules_dir / "README.md").exists()
        assert not (modules_dir / "Targets").exists()

    def test_creates_nested_subdirectories(self, tmp_path):
        """Deeply nested module paths are created automatically."""
        modules_dir = tmp_path / "Modules"
        modules_dir.mkdir()

        zip_bytes = _build_fake_zip({
            "KapeFiles-master/Modules/A/B/C/Deep.mkape": b"deep",
        })
        fake_open = lambda url: _FakeResponse(zip_bytes)

        kape_modules.sync_modules(modules_dir, _urlopen=fake_open)

        assert (modules_dir / "A" / "B" / "C" / "Deep.mkape").exists()

    def test_download_failure_raises(self, tmp_path):
        """A network error propagates as an exception."""
        modules_dir = tmp_path / "Modules"
        modules_dir.mkdir()

        def failing_open(url):
            raise ConnectionError("no network")

        with pytest.raises(ConnectionError):
            kape_modules.sync_modules(modules_dir, _urlopen=failing_open)

    def test_bad_zip_raises(self, tmp_path):
        """Non-ZIP data raises BadZipFile."""
        modules_dir = tmp_path / "Modules"
        modules_dir.mkdir()

        fake_open = lambda url: _FakeResponse(b"not a zip")

        with pytest.raises(zipfile.BadZipFile):
            kape_modules.sync_modules(modules_dir, _urlopen=fake_open)


# ---------------------------------------------------------------------------
# CLI integration tests
# ---------------------------------------------------------------------------


class TestMainMsync:
    """End-to-end: main() with --msync should call sync_modules and exit."""

    def test_main_msync(self, tmp_path):
        modules_dir = tmp_path / "Modules"

        zip_bytes = _build_fake_zip({
            "KapeFiles-master/Modules/Cat/Mod.mkape": b"data",
        })
        fake_open = lambda url: _FakeResponse(zip_bytes)

        with patch.object(kape_modules, "sync_modules", wraps=kape_modules.sync_modules) as mock_sync:
            with patch("kape_modules.urllib.request.urlopen", side_effect=fake_open):
                kape_modules.main([
                    "--msync",
                    "--mpath", str(modules_dir),
                ])
            mock_sync.assert_called_once()

        assert (modules_dir / "Cat" / "Mod.mkape").exists()

    def test_main_msync_creates_modules_dir(self, tmp_path):
        """--msync creates the Modules directory if it does not exist."""
        modules_dir = tmp_path / "NewModules"
        assert not modules_dir.exists()

        zip_bytes = _build_fake_zip({
            "KapeFiles-master/Modules/Cat/Mod.mkape": b"data",
        })
        fake_open = lambda url: _FakeResponse(zip_bytes)

        with patch("kape_modules.urllib.request.urlopen", side_effect=fake_open):
            kape_modules.main([
                "--msync",
                "--mpath", str(modules_dir),
            ])

        assert modules_dir.exists()
        assert (modules_dir / "Cat" / "Mod.mkape").exists()

    def test_msync_argument_exists(self):
        """--msync is a recognized argument."""
        parser = kape_modules.build_argument_parser()
        args = parser.parse_args(["--msync"])
        assert args.msync is True

    def test_msync_default_false(self):
        """--msync defaults to False."""
        parser = kape_modules.build_argument_parser()
        args = parser.parse_args([])
        assert args.msync is False
