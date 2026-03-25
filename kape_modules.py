#!/usr/bin/env python3
"""
KAPE Module Runner — Python implementation of the KAPE Module component.

Loads .mkape module files and executes the configured processors against a
source directory, saving output to a destination directory.

The Modules directory should be placed next to this script (or specify an
alternate location with --mpath).  Download KAPE module files from:
    https://github.com/EricZimmerman/KapeFiles/tree/master/Modules

Usage examples:
    python kape_modules.py --msource C:\\evidence --mdest C:\\output \\
        --module EvtxECmd

    python kape_modules.py --msource /mnt/evidence --mdest /tmp/output \\
        --module EvtxECmd,PECmd --mef csv

    python kape_modules.py --mlist --mdetail
    python kape_modules.py --mlist --mpath /path/to/Modules
"""

import argparse
import datetime
import hashlib
import io
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import traceback
import urllib.request
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

try:
    import yaml
except ImportError:  # pragma: no cover
    print(
        "PyYAML is required.  Install with: pip install pyyaml",
        file=sys.stderr,
    )
    sys.exit(1)


# ---------------------------------------------------------------------------
# Platform detection and executable mapping
# ---------------------------------------------------------------------------

def _detect_platform() -> str:
    """Return a normalised platform name: ``windows``, ``linux``, or ``darwin``."""
    if sys.platform == "win32":
        return "windows"
    if sys.platform == "darwin":
        return "darwin"
    # Treat all other POSIX-like systems (FreeBSD, OpenBSD, etc.) as linux
    # since the executable mapping is typically the same.
    return "linux"


CURRENT_PLATFORM: str = _detect_platform()


def load_platform_map(map_path: Optional[Path] = None) -> dict:
    """
    Load a platform mapping YAML file.

    The file maps Windows executable names to their equivalents on other
    platforms.  Expected format::

        executables:
          pecmd.exe:
            linux:
              executable: dotnet
              command_line: "pecmd.dll {original_args}"
            darwin:
              executable: dotnet
              command_line: "pecmd.dll {original_args}"

    Returns an empty dict if *map_path* is ``None`` or the file does not
    exist.
    """
    if map_path is None:
        return {}
    if not map_path.is_file():
        logging.debug("Platform map file not found: %s", map_path)
        return {}
    try:
        with open(map_path, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        logging.info("Loaded platform map from: %s", map_path)
        return data
    except Exception as exc:  # noqa: BLE001
        logging.warning("Failed to load platform map '%s': %s", map_path, exc)
        return {}


def apply_platform_mapping(
    executable: str,
    cmdline: str,
    platform_map: dict,
    current_platform: Optional[str] = None,
) -> Tuple[str, str]:
    """
    Replace *executable* and *cmdline* when a platform mapping exists.

    Looks up *executable* (case-insensitive) in the ``executables`` section
    of *platform_map*.  If the current platform has a mapping entry the
    executable (and optionally the command line) are replaced.

    In the ``command_line`` value the placeholder ``{original_args}`` is
    replaced with the original *cmdline* string.

    Returns the (possibly updated) ``(executable, cmdline)`` tuple.
    """
    if not platform_map:
        return executable, cmdline

    if current_platform is None:
        current_platform = CURRENT_PLATFORM

    # Windows is the native platform – no mapping needed.
    if current_platform == "windows":
        return executable, cmdline

    executables_map: dict = platform_map.get("executables") or {}

    # Case-insensitive lookup
    exe_lower = executable.lower()
    mapping = None
    for key, value in executables_map.items():
        if key.lower() == exe_lower:
            mapping = value
            break

    if mapping is None:
        return executable, cmdline

    platform_entry: dict = mapping.get(current_platform) or {}
    if not platform_entry:
        return executable, cmdline

    new_executable: str = platform_entry.get("executable") or executable
    new_cmdline_template: Optional[str] = platform_entry.get("command_line")

    if new_cmdline_template is not None:
        new_cmdline = new_cmdline_template.replace("{original_args}", cmdline)
    else:
        new_cmdline = cmdline

    logging.info(
        "Platform mapping applied: %s -> %s (platform=%s)",
        executable, new_executable, current_platform,
    )
    return new_executable, new_cmdline


# ---------------------------------------------------------------------------
# Module directory helpers
# ---------------------------------------------------------------------------


def get_modules_dir(script_dir: Path, mpath: Optional[str] = None) -> Path:
    """Return the Modules directory path."""
    if mpath:
        return Path(mpath).resolve()
    return script_dir / "Modules"


# ---------------------------------------------------------------------------
# Module sync
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Module file I/O
# ---------------------------------------------------------------------------


def load_module(module_file: Path) -> dict:
    """Parse a .mkape YAML file and return its contents as a dict."""
    with open(module_file, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def _is_disabled(path: Path) -> bool:
    """Return True if *path* lives inside a ``!Disabled`` directory."""
    return any(part.startswith("!Disabled") for part in path.parts)


def iter_module_files(modules_dir: Path) -> List[Path]:
    """Yield all .mkape files under *modules_dir*, skipping ``!Disabled``."""
    return sorted(
        mkape
        for mkape in modules_dir.rglob("*.mkape")
        if not _is_disabled(mkape)
    )


def find_module_files(modules_dir: Path, module_name: str) -> List[Path]:
    """Return .mkape files whose stem matches *module_name* (case-insensitive)."""
    lower = module_name.lower()
    return [
        mkape
        for mkape in iter_module_files(modules_dir)
        if mkape.stem.lower() == lower
    ]


# ---------------------------------------------------------------------------
# Processor selection
# ---------------------------------------------------------------------------


def get_processor(module_data: dict, export_format: Optional[str]) -> Optional[dict]:
    """
    Select the processor entry to use.

    Priority:
    1. Processor whose ExportFormat matches *export_format* (if supplied).
    2. Processor whose ExportFormat matches the module-level default ExportFormat.
    3. First processor in the list.
    """
    processors = [p for p in (module_data.get("Processors") or []) if p]
    if not processors:
        return None

    if export_format:
        fmt = export_format.lower()
        for proc in processors:
            if proc.get("ExportFormat", "").lower() == fmt:
                return proc

    default_fmt = (module_data.get("ExportFormat") or "").lower()
    if default_fmt:
        for proc in processors:
            if proc.get("ExportFormat", "").lower() == default_fmt:
                return proc

    return processors[0]


# ---------------------------------------------------------------------------
# File matching (FileMask)
# ---------------------------------------------------------------------------


def find_matching_files(source_path: Path, file_mask: str) -> List[Path]:
    """
    Return all files under *source_path* that match *file_mask*.

    *file_mask* can be:
    - A glob pattern (e.g. ``*.jpg``, ``Foo*.txt``).
    - A regex pattern prefixed with ``regex:``
      (e.g. ``regex:(2019|DSC).+\\.jpg``).
    """
    results: List[Path] = []
    if file_mask.startswith("regex:"):
        pattern = r"\A" + file_mask[6:].strip() + r"\z"
        regex = re.compile(pattern, re.IGNORECASE)
        for f in source_path.rglob("*"):
            if f.is_file() and regex.match(f.name):
                results.append(f)
    else:
        mask = file_mask.lstrip("\\/")
        for f in source_path.rglob(mask):
            if f.is_file():
                results.append(f)
    return results


# ---------------------------------------------------------------------------
# Executable discovery
# ---------------------------------------------------------------------------


def find_executable(executable: str, modules_dir: Path, module_name: str) -> str:
    """
    Locate *executable* using KAPE's lookup order:

    1. ``<modules_dir>/<module_name>/<executable>``
    2. ``<modules_dir>/bin/<executable>``
    3. The name as-is (rely on the system PATH / absolute path).
    """
    named_dir = modules_dir / module_name / executable
    if named_dir.exists():
        return str(named_dir)

    bin_dir = modules_dir / "bin" / executable
    if bin_dir.exists():
        return str(bin_dir)

    return executable


# ---------------------------------------------------------------------------
# Variable substitution
# ---------------------------------------------------------------------------


def substitute_variables(text: str, variables: Dict[str, str]) -> str:
    """Replace ``%key%`` placeholders in *text* with the corresponding values."""
    for key, value in variables.items():
        text = text.replace(f"%{key}%", str(value))
    return text


def build_base_vars(
    source_path: Path,
    dest_dir: str,
    script_dir: Path,
    mvars: Dict[str, str],
) -> Dict[str, str]:
    """Build the standard KAPE variable dict, merged with user-supplied *mvars*."""
    base: Dict[str, str] = {
        "sourceDirectory": str(source_path),
        "destinationDirectory": dest_dir,
        "sourceDriveLetter": source_path.drive,
        "kapeDirectory": str(script_dir),
        "d": datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S"),
        "guid": str(uuid.uuid4()),
    }
    base.update(mvars)
    return base


# ---------------------------------------------------------------------------
# Command execution
# ---------------------------------------------------------------------------


def _unique_export_path(dest_dir: str, export_file: str) -> str:
    """Return a unique path for *export_file* inside *dest_dir*."""
    path = os.path.join(dest_dir, export_file)
    if not os.path.exists(path):
        return path
    base, ext = os.path.splitext(export_file)
    counter = 1
    while os.path.exists(os.path.join(dest_dir, f"{base}{counter}{ext}")):
        counter += 1
    return os.path.join(dest_dir, f"{base}{counter}{ext}")


def _build_command_string(executable: str, cmdline: str) -> str:
    """Compose the full shell command, quoting *executable* when it contains spaces."""
    if " " in executable and not executable.startswith('"'):
        return f'"{executable}" {cmdline}'
    return f"{executable} {cmdline}"


def execute_command(
    executable: str,
    cmdline: str,
    dest_dir: str,
    export_file: Optional[str],
    append: bool,
    wait_timeout: int,
    debug: bool,
    dry_run: bool = False,
) -> None:
    """
    Run *executable* with *cmdline*.

    If *export_file* is set, stdout and stderr are redirected to that file
    inside *dest_dir* (appended when *append* is True, otherwise a unique
    file name is chosen to avoid collisions).

    *wait_timeout* is the maximum number of **minutes** to wait (0 = unlimited).

    When *dry_run* is True the command is logged but **not** executed.
    """
    full_cmd = _build_command_string(executable, cmdline)

    if dry_run:
        logging.info("    [DRY RUN] Would execute: %s", full_cmd)
        if export_file:
            logging.info("    [DRY RUN] Output would be written to: %s",
                         os.path.join(dest_dir, export_file))
        return

    logging.info("    Executing: %s", full_cmd)

    timeout_secs: Optional[float] = (wait_timeout * 60) if wait_timeout > 0 else None

    try:
        if export_file:
            export_path = (
                os.path.join(dest_dir, export_file)
                if append
                else _unique_export_path(dest_dir, export_file)
            )
            file_mode = "a" if append else "w"
            with open(export_path, file_mode, encoding="utf-8", errors="replace") as fout:
                result = subprocess.run(
                    full_cmd,
                    shell=True,
                    stdout=fout,
                    stderr=fout,
                    timeout=timeout_secs,
                )
            logging.info("    Output written to: %s", export_path)
        else:
            result = subprocess.run(
                full_cmd,
                shell=True,
                stdout=None if debug else subprocess.PIPE,
                stderr=None if debug else subprocess.PIPE,
                timeout=timeout_secs,
            )

        if result.returncode != 0:
            logging.warning("    Process exited with code %d", result.returncode)
        else:
            logging.debug("    Process completed successfully (exit 0)")

    except subprocess.TimeoutExpired:
        logging.warning("    Module timed out after %d minute(s)", wait_timeout)
    except FileNotFoundError:
        logging.error("    Executable not found: %s", executable)
    except Exception as exc:  # noqa: BLE001
        logging.error("    Unexpected error executing command: %s", exc)


# ---------------------------------------------------------------------------
# Module runner
# ---------------------------------------------------------------------------


def run_module(
    module_name: str,
    module_data: dict,
    module_file: Path,
    modules_dir: Path,
    msource: str,
    mdest: str,
    mef: Optional[str],
    mvars: Dict[str, str],
    debug: bool,
    processed_ids: Set[str],
    dry_run: bool = False,
    num_threads: int = 1,
    platform_map: Optional[dict] = None,
) -> None:
    """
    Execute a single module (or expand a compound module).

    *processed_ids* tracks module GUIDs that have already run, preventing
    duplicate execution when compound modules reference the same module.

    When *dry_run* is True, commands are logged but not executed.

    *num_threads* controls how many worker threads are used for concurrent
    execution when a module processes multiple files (FileMask).  Defaults
    to 1 (sequential execution).

    *platform_map*, when provided, is used to replace executables and
    command lines with platform-specific alternatives (see
    :func:`apply_platform_mapping`).
    """
    module_id: str = module_data.get("Id") or ""
    if module_id and module_id in processed_ids:
        logging.debug(
            "Module '%s' (Id=%s) already processed — skipping", module_name, module_id
        )
        return
    if module_id:
        processed_ids.add(module_id)

    description = module_data.get("Description") or module_name
    category = module_data.get("Category") or "Misc"

    logging.info("Processing module: %s | %s", module_name, description)

    # ------------------------------------------------------------------
    # Compound module: references other modules by name
    # ------------------------------------------------------------------
    sub_module_names: List[str] = module_data.get("Modules") or []
    if sub_module_names:
        for sub_name in sub_module_names:
            if sub_name.strip().upper() == "!ALL":
                for sub_file in iter_module_files(modules_dir):
                    try:
                        sub_data = load_module(sub_file)
                        run_module(
                            sub_file.stem,
                            sub_data,
                            sub_file,
                            modules_dir,
                            msource,
                            mdest,
                            mef,
                            mvars,
                            debug,
                            processed_ids,
                            dry_run,
                            num_threads,
                            platform_map,
                        )
                    except Exception as exc:  # noqa: BLE001
                        logging.error(
                            "Error in sub-module '%s': %s", sub_file.stem, exc
                        )
            else:
                sub_files = find_module_files(modules_dir, sub_name.strip())
                if not sub_files:
                    logging.warning("Sub-module '%s' not found", sub_name)
                    continue
                for sub_file in sub_files:
                    try:
                        sub_data = load_module(sub_file)
                        run_module(
                            sub_name.strip(),
                            sub_data,
                            sub_file,
                            modules_dir,
                            msource,
                            mdest,
                            mef,
                            mvars,
                            debug,
                            processed_ids,
                            dry_run,
                            num_threads,
                            platform_map,
                        )
                    except Exception as exc:  # noqa: BLE001
                        logging.error(
                            "Error in sub-module '%s': %s", sub_name, exc
                        )
        return

    # ------------------------------------------------------------------
    # Regular module: select processor and execute
    # ------------------------------------------------------------------
    processor = get_processor(module_data, mef)
    if not processor:
        logging.warning("No processor available for module '%s'", module_name)
        return

    executable_name: str = processor.get("Executable") or ""
    if not executable_name:
        logging.warning("Processor has no Executable for module '%s'", module_name)
        return

    cmdline_template: str = processor.get("CommandLine") or ""
    export_file: Optional[str] = processor.get("ExportFile")
    append: bool = bool(processor.get("Append", False))
    wait_timeout: int = module_data.get("WaitTimeout") or 0

    dest_dir = os.path.join(mdest, category)
    if not dry_run:
        os.makedirs(dest_dir, exist_ok=True)

    source_path = Path(msource).resolve()
    script_dir = Path(__file__).parent.resolve()
    base_vars = build_base_vars(source_path, dest_dir, script_dir, mvars)

    file_mask: Optional[str] = module_data.get("FileMask")
    if file_mask:
        matching_files = find_matching_files(source_path, file_mask)
        if not matching_files:
            logging.warning(
                "No files matching FileMask '%s' found in %s", file_mask, source_path
            )
            return

        def _process_file(source_file: Path) -> None:
            file_vars = dict(base_vars)
            file_vars["sourceFile"] = str(source_file)
            file_vars["fileName"] = source_file.name
            try:
                rel_parts = source_file.relative_to(source_path).parts
                file_vars["sourceDirectoryBase"] = rel_parts[0] if rel_parts else ""
            except ValueError:
                file_vars["sourceDirectoryBase"] = ""

            cmdline = substitute_variables(cmdline_template, file_vars)
            exe = find_executable(executable_name, modules_dir, module_name)
            exe, cmdline = apply_platform_mapping(exe, cmdline, platform_map or {})
            execute_command(exe, cmdline, dest_dir, export_file, append, wait_timeout, debug, dry_run)

        if num_threads > 1:
            with ThreadPoolExecutor(max_workers=num_threads) as executor:
                futures = {
                    executor.submit(_process_file, sf): sf
                    for sf in matching_files
                }
                for future in as_completed(futures):
                    try:
                        future.result()
                    except Exception as exc:  # noqa: BLE001
                        logging.error(
                            "Error processing file '%s': %s",
                            futures[future], exc,
                        )
        else:
            for source_file in matching_files:
                _process_file(source_file)
    else:
        cmdline = substitute_variables(cmdline_template, base_vars)
        exe = find_executable(executable_name, modules_dir, module_name)
        exe, cmdline = apply_platform_mapping(exe, cmdline, platform_map or {})
        execute_command(exe, cmdline, dest_dir, export_file, append, wait_timeout, debug, dry_run)


# ---------------------------------------------------------------------------
# Module listing (--mlist / --mdetail)
# ---------------------------------------------------------------------------


def list_modules(modules_dir: Path, detail: bool = False) -> None:
    """Print available modules.  When *detail* is True, show full metadata."""
    all_modules = iter_module_files(modules_dir)
    if not all_modules:
        print("  No modules found.")
        return

    for mkape in all_modules:
        try:
            data = load_module(mkape)
        except Exception as exc:  # noqa: BLE001
            print(f"  {mkape.stem}: [Error loading: {exc}]")
            continue

        rel = mkape.relative_to(modules_dir)
        if detail:
            print(f"Name        : {mkape.stem}")
            print(f"Path        : {rel}")
            print(f"Description : {data.get('Description', 'N/A')}")
            print(f"Category    : {data.get('Category', 'N/A')}")
            print(f"Author      : {data.get('Author', 'N/A')}")
            print(f"Version     : {data.get('Version', 'N/A')}")
            print(f"ExportFormat: {data.get('ExportFormat', 'N/A')}")
            print(f"BinaryUrl   : {data.get('BinaryUrl', 'N/A')}")
            processors = [p for p in (data.get("Processors") or []) if p]
            for idx, proc in enumerate(processors):
                exe = proc.get("Executable", "")
                named_ok = (modules_dir / mkape.stem / exe).exists()
                bin_ok = (modules_dir / "bin" / exe).exists()
                abs_ok = Path(exe).is_absolute() and Path(exe).exists()
                status = "OK" if (named_ok or bin_ok or abs_ok) else "MISSING"
                print(f"Processor {idx} : {exe} [{status}]")
                print(f"  CommandLine : {proc.get('CommandLine', '')}")
                print(f"  ExportFormat: {proc.get('ExportFormat', '')}")
                if proc.get("ExportFile"):
                    print(f"  ExportFile  : {proc['ExportFile']}")
            sub_modules = data.get("Modules") or []
            if sub_modules:
                print(f"Sub-modules : {', '.join(sub_modules)}")
            print()
        else:
            print(f"  {mkape.stem}: {data.get('Description', 'N/A')}")


# ---------------------------------------------------------------------------
# Module syncing (--msync)
# ---------------------------------------------------------------------------

KAPEFILES_DEFAULT_URL = (
    "https://github.com/EricZimmerman/KapeFiles/archive/master.zip"
)

# Directories that are never overwritten or removed by sync.
_SYNC_PRESERVE_DIRS = {"!disabled", "!local", "bin", "sample"}


def _sha1(path: Path) -> str:
    """Return the hex SHA-1 digest of a file."""
    h = hashlib.sha1()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def _sha1_bytes(data: bytes) -> str:
    """Return the hex SHA-1 digest of raw bytes."""
    return hashlib.sha1(data).hexdigest()


def _is_preserved_dir(rel_path: Path) -> bool:
    """Return True if *rel_path* falls inside a preserved directory."""
    for part in rel_path.parts:
        if part.lower() in _SYNC_PRESERVE_DIRS:
            return True
    return False


def sync_modules(modules_dir: Path, url: Optional[str] = None) -> None:
    """Download module files from the KapeFiles repository and update *modules_dir*.

    Behaviour mirrors KAPE's ``--sync`` flag:

    * ``.mkape`` files from the repo are added or updated (SHA-1 comparison).
    * Local-only ``.mkape`` files are moved to the ``!Local`` directory.
    * The ``!Disabled``, ``!Local``, ``bin``, and ``Sample`` directories are
      never modified by the sync process.
    """
    download_url = url or KAPEFILES_DEFAULT_URL
    logging.info("Syncing modules from %s", download_url)

    # ------------------------------------------------------------------
    # 1. Download the zip archive
    # ------------------------------------------------------------------
    try:
        req = urllib.request.Request(
            download_url,
            headers={"User-Agent": "pykapetargetexec-sync"},
        )
        with urllib.request.urlopen(req) as resp:  # noqa: S310
            archive_bytes = resp.read()
    except Exception as exc:
        logging.error("Failed to download KapeFiles archive: %s", exc)
        return

    # ------------------------------------------------------------------
    # 2. Extract to a temporary directory
    # ------------------------------------------------------------------
    try:
        zf = zipfile.ZipFile(io.BytesIO(archive_bytes))
    except zipfile.BadZipFile as exc:
        logging.error("Downloaded file is not a valid zip archive: %s", exc)
        return

    with tempfile.TemporaryDirectory() as tmpdir:
        zf.extractall(tmpdir)

        # The zip usually contains a single top-level directory such as
        # "KapeFiles-master".  Locate the Modules sub-directory inside it.
        extracted_root = Path(tmpdir)
        candidates = list(extracted_root.iterdir())
        if len(candidates) == 1 and candidates[0].is_dir():
            extracted_root = candidates[0]

        remote_modules_dir = extracted_root / "Modules"
        if not remote_modules_dir.is_dir():
            logging.error(
                "Modules directory not found in the downloaded archive"
            )
            return

        # ------------------------------------------------------------------
        # 3. Build an index of remote .mkape files (relative path -> bytes)
        # ------------------------------------------------------------------
        remote_files: Dict[str, bytes] = {}
        for mkape in remote_modules_dir.rglob("*.mkape"):
            rel = mkape.relative_to(remote_modules_dir)
            if _is_preserved_dir(rel):
                continue
            remote_files[str(rel)] = mkape.read_bytes()

        # ------------------------------------------------------------------
        # 4. Build an index of existing local .mkape files
        # ------------------------------------------------------------------
        modules_dir.mkdir(parents=True, exist_ok=True)
        local_files: Dict[str, Path] = {}
        for mkape in modules_dir.rglob("*.mkape"):
            rel = mkape.relative_to(modules_dir)
            if _is_preserved_dir(rel):
                continue
            local_files[str(rel)] = mkape

        # ------------------------------------------------------------------
        # 5. Add new / update changed modules
        # ------------------------------------------------------------------
        added = 0
        updated = 0
        for rel_str, content in sorted(remote_files.items()):
            dest_path = modules_dir / rel_str
            if rel_str in local_files:
                if _sha1(local_files[rel_str]) == _sha1_bytes(content):
                    continue  # identical – nothing to do
                logging.info("  Updated: %s", rel_str)
                dest_path.parent.mkdir(parents=True, exist_ok=True)
                dest_path.write_bytes(content)
                updated += 1
            else:
                logging.info("  New: %s", rel_str)
                dest_path.parent.mkdir(parents=True, exist_ok=True)
                dest_path.write_bytes(content)
                added += 1

        # ------------------------------------------------------------------
        # 6. Move local-only modules to !Local
        # ------------------------------------------------------------------
        moved = 0
        local_dir = modules_dir / "!Local"
        for rel_str, local_path in sorted(local_files.items()):
            if rel_str not in remote_files:
                local_dir.mkdir(parents=True, exist_ok=True)
                dest_path = local_dir / Path(rel_str).name
                if dest_path.exists():
                    # Avoid overwriting an existing !Local file
                    base = dest_path.stem
                    ext = dest_path.suffix
                    counter = 1
                    while dest_path.exists():
                        dest_path = local_dir / f"{base}{counter}{ext}"
                        counter += 1
                logging.info("  Moved to !Local: %s", rel_str)
                shutil.move(str(local_path), str(dest_path))
                moved += 1

        # ------------------------------------------------------------------
        # 7. Copy non-mkape files from remote Modules dir (guides, templates)
        # ------------------------------------------------------------------
        for item in remote_modules_dir.iterdir():
            if item.is_file() and item.suffix.lower() != ".mkape":
                dest = modules_dir / item.name
                remote_content = item.read_bytes()
                if dest.exists() and _sha1(dest) == _sha1_bytes(remote_content):
                    continue
                dest.write_bytes(remote_content)

        logging.info(
            "Sync complete: %d new, %d updated, %d moved to !Local",
            added, updated, moved,
        )


# ---------------------------------------------------------------------------
# --mvars parsing
# ---------------------------------------------------------------------------


def parse_mvars(mvars_str: str) -> Dict[str, str]:
    """
    Parse a KAPE-style ``--mvars`` string into a dict.

    Format: ``key1:value1,key2:value2`` (comma or semicolon separated).
    """
    result: Dict[str, str] = {}
    if not mvars_str:
        return result
    for pair in re.split(r"[,;]", mvars_str):
        pair = pair.strip()
        if ":" in pair:
            key, _, value = pair.partition(":")
            result[key.strip()] = value.strip()
    return result


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="kape_modules.py",
        description=(
            "KAPE Module Runner — Python implementation of the KAPE Module component.\n"
            "Runs .mkape module files against a source directory.\n\n"
            "Download modules from:\n"
            "  https://github.com/EricZimmerman/KapeFiles/tree/master/Modules\n\n"
            "Examples:\n"
            "  kape_modules.py --msource C:\\evidence --mdest C:\\output"
            " --module EvtxECmd\n"
            "  kape_modules.py --msource /mnt/evidence --mdest /tmp/out"
            " --module EvtxECmd,PECmd --mef csv\n"
            "  kape_modules.py --mlist --mdetail"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument(
        "--msource",
        metavar="PATH",
        help="Source directory containing files to process.",
    )
    parser.add_argument(
        "--mdest",
        metavar="PATH",
        help="Destination directory where module output is written.",
    )
    parser.add_argument(
        "--module",
        metavar="NAMES",
        help=(
            "Comma-separated list of module names to run "
            "(without the .mkape extension)."
        ),
    )
    parser.add_argument(
        "--mef",
        metavar="FORMAT",
        help="Override the module export format (e.g. csv, json, xml).",
    )
    parser.add_argument(
        "--mvars",
        metavar="VARS",
        help=(
            "Module variables as key:value pairs separated by commas "
            "(e.g. var1:value1,var2:value2)."
        ),
    )
    parser.add_argument(
        "--mpath",
        metavar="PATH",
        help=(
            "Path to the Modules directory.  "
            "Defaults to a 'Modules' folder next to this script."
        ),
    )
    parser.add_argument(
        "--mlist",
        action="store_true",
        help="List available modules and exit.",
    )
    parser.add_argument(
        "--mdetail",
        action="store_true",
        help="Show full module details including binary status (use with --mlist).",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable verbose debug output.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be executed without actually running any commands.",
    )
    parser.add_argument(
        "--mthreads",
        metavar="N",
        type=int,
        default=1,
        help=(
            "Number of worker threads for concurrent execution.  "
            "When greater than 1, file-level and module-level processing "
            "runs in parallel using a thread pool (default: 1)."
        ),
    )
    parser.add_argument(
        "--post-process",
        metavar="NAMES",
        help=(
            "Comma-separated list of module names to run after all --module "
            "modules have completed.  Post-process modules receive --mdest "
            "as their source directory and write output back to --mdest."
        ),
    )
    parser.add_argument(
        "--msync",
        nargs="?",
        const=KAPEFILES_DEFAULT_URL,
        default=None,
        metavar="URL",
        help=(
            "Sync modules from the KapeFiles GitHub repository and exit. "
            "Optionally provide a URL to a custom fork's zip archive "
            "(default: %(const)s)."
        ),
    )
    parser.add_argument(
        "--platform-map",
        metavar="PATH",
        help=(
            "Path to a platform_map.yaml file that maps Windows executables "
            "to their equivalents on other platforms.  If not specified, the "
            "runner looks for 'platform_map.yaml' next to this script."
        ),
    )
    return parser


def main(argv: Optional[List[str]] = None) -> None:
    parser = build_argument_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    script_dir = Path(__file__).parent.resolve()
    modules_dir = get_modules_dir(script_dir, args.mpath)

    # ------------------------------------------------------------------
    # --msync: sync modules from KapeFiles repository and exit
    # ------------------------------------------------------------------
    if args.msync is not None:
        sync_modules(modules_dir, args.msync)
        return

    # ------------------------------------------------------------------
    # --mlist: enumerate modules and exit
    # ------------------------------------------------------------------
    if args.mlist:
        if not modules_dir.exists():
            print(f"Modules directory not found: {modules_dir}", file=sys.stderr)
            sys.exit(1)
        print(f"Modules in: {modules_dir}\n")
        list_modules(modules_dir, detail=args.mdetail)
        return

    # ------------------------------------------------------------------
    # Module execution: validate required arguments
    # ------------------------------------------------------------------
    errors: List[str] = []
    if not args.msource:
        errors.append("--msource is required")
    if not args.mdest:
        errors.append("--mdest is required")
    if not args.module:
        errors.append("--module is required")
    if errors:
        for err in errors:
            parser.error(err)

    if not modules_dir.exists():
        logging.error("Modules directory not found: %s", modules_dir)
        logging.error(
            "Download KAPE modules from "
            "https://github.com/EricZimmerman/KapeFiles "
            "and place the Modules folder next to this script, "
            "or use --mpath to specify its location."
        )
        sys.exit(1)

    if not os.path.isdir(args.msource):
        logging.error("Source directory does not exist: %s", args.msource)
        sys.exit(1)

    if not args.dry_run:
        os.makedirs(args.mdest, exist_ok=True)

    mvars = parse_mvars(args.mvars or "")
    module_names = [n.strip() for n in args.module.split(",") if n.strip()]
    processed_ids: Set[str] = set()
    num_threads: int = max(1, args.mthreads)

    # ------------------------------------------------------------------
    # Platform detection and executable mapping
    # ------------------------------------------------------------------
    logging.info("Detected platform: %s", CURRENT_PLATFORM)
    if args.platform_map:
        pmap_path = Path(args.platform_map).resolve()
    else:
        pmap_path = script_dir / "platform_map.yaml"
    platform_map = load_platform_map(pmap_path)

    def _run_single_module(module_name: str, module_file: Path) -> None:
        try:
            module_data = load_module(module_file)
            run_module(
                module_name,
                module_data,
                module_file,
                modules_dir,
                args.msource,
                args.mdest,
                args.mef,
                mvars,
                args.debug,
                processed_ids,
                args.dry_run,
                num_threads,
                platform_map,
            )
        except Exception as exc:  # noqa: BLE001
            logging.error("Error processing module '%s': %s", module_name, exc)
            if args.debug:
                traceback.print_exc()

    # Collect (module_name, module_file) pairs to execute
    tasks: List[tuple] = []
    for module_name in module_names:
        module_files = find_module_files(modules_dir, module_name)
        if not module_files:
            logging.warning("Module '%s' not found in %s", module_name, modules_dir)
            continue
        for module_file in module_files:
            tasks.append((module_name, module_file))

    if num_threads > 1 and len(tasks) > 1:
        with ThreadPoolExecutor(max_workers=num_threads) as executor:
            futures = {
                executor.submit(_run_single_module, name, mfile): name
                for name, mfile in tasks
            }
            for future in as_completed(futures):
                try:
                    future.result()
                except Exception as exc:  # noqa: BLE001
                    logging.error(
                        "Error processing module '%s': %s", futures[future], exc
                    )
    else:
        for module_name, module_file in tasks:
            _run_single_module(module_name, module_file)

    # ------------------------------------------------------------------
    # --post-process: run modules after all --module tasks have finished.
    # The source directory for post-process modules is --mdest.
    # ------------------------------------------------------------------
    if args.post_process:
        logging.info("Starting post-process modules.")
        post_names = [n.strip() for n in args.post_process.split(",") if n.strip()]
        post_processed_ids: Set[str] = set()

        def _run_post_module(module_name: str, module_file: Path) -> None:
            try:
                module_data = load_module(module_file)
                run_module(
                    module_name,
                    module_data,
                    module_file,
                    modules_dir,
                    args.mdest,
                    args.mdest,
                    args.mef,
                    mvars,
                    args.debug,
                    post_processed_ids,
                    args.dry_run,
                    num_threads,
                    platform_map,
                )
            except Exception as exc:  # noqa: BLE001
                logging.error(
                    "Error processing post-process module '%s': %s",
                    module_name, exc,
                )
                if args.debug:
                    traceback.print_exc()

        post_tasks: List[tuple] = []
        for post_name in post_names:
            post_files = find_module_files(modules_dir, post_name)
            if not post_files:
                logging.warning(
                    "Post-process module '%s' not found in %s",
                    post_name, modules_dir,
                )
                continue
            for post_file in post_files:
                post_tasks.append((post_name, post_file))

        if num_threads > 1 and len(post_tasks) > 1:
            with ThreadPoolExecutor(max_workers=num_threads) as executor:
                futures = {
                    executor.submit(_run_post_module, name, mfile): name
                    for name, mfile in post_tasks
                }
                for future in as_completed(futures):
                    try:
                        future.result()
                    except Exception as exc:  # noqa: BLE001
                        logging.error(
                            "Error processing post-process module '%s': %s",
                            futures[future], exc,
                        )
        else:
            for post_name, post_file in post_tasks:
                _run_post_module(post_name, post_file)

    logging.info("Done.")


if __name__ == "__main__":
    main()
