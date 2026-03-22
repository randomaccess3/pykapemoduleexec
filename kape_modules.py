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
import io
import logging
import os
import re
import subprocess
import sys
import traceback
import urllib.request
import uuid
import zipfile
from pathlib import Path
from typing import Dict, List, Optional, Set

try:
    import yaml
except ImportError:  # pragma: no cover
    print(
        "PyYAML is required.  Install with: pip install pyyaml",
        file=sys.stderr,
    )
    sys.exit(1)


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

KAPEFILES_ZIP_URL = (
    "https://github.com/EricZimmerman/KapeFiles/archive/refs/heads/master.zip"
)
_KAPEFILES_MODULES_PREFIX = "KapeFiles-master/Modules/"


def sync_modules(
    modules_dir: Path,
    zip_url: str = KAPEFILES_ZIP_URL,
    _urlopen=None,
) -> None:
    """
    Sync module files from the KapeFiles GitHub repository.

    Downloads the repository archive, extracts the ``Modules/`` directory and
    updates the local *modules_dir* with the latest ``.mkape`` files and
    directory structure.  Files that exist only locally are left untouched.

    The *_urlopen* parameter is exposed for testing so callers can inject a
    fake HTTP response without hitting the network.
    """
    opener = _urlopen or urllib.request.urlopen

    logging.info("Syncing modules from %s …", zip_url)
    logging.info("Local modules directory: %s", modules_dir)

    # ------------------------------------------------------------------
    # Download the ZIP archive
    # ------------------------------------------------------------------
    try:
        resp = opener(zip_url)
        zip_bytes = resp.read()
    except Exception as exc:
        logging.error("Failed to download KapeFiles archive: %s", exc)
        raise

    # ------------------------------------------------------------------
    # Extract Modules/ entries
    # ------------------------------------------------------------------
    try:
        zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
    except zipfile.BadZipFile as exc:
        logging.error("Downloaded file is not a valid ZIP archive: %s", exc)
        raise

    prefix = _KAPEFILES_MODULES_PREFIX
    added = 0
    updated = 0
    unchanged = 0

    for info in zf.infolist():
        # Only process entries under the Modules/ directory
        if not info.filename.startswith(prefix):
            continue

        rel = info.filename[len(prefix):]
        if not rel:
            continue

        dest = modules_dir / rel

        # Directory entry
        if info.filename.endswith("/"):
            dest.mkdir(parents=True, exist_ok=True)
            continue

        # File entry — ensure parent directory exists
        dest.parent.mkdir(parents=True, exist_ok=True)

        new_content = zf.read(info.filename)
        if dest.exists():
            existing = dest.read_bytes()
            if existing == new_content:
                unchanged += 1
                logging.debug("Unchanged: %s", rel)
                continue
            dest.write_bytes(new_content)
            updated += 1
            logging.info("Updated: %s", rel)
        else:
            dest.write_bytes(new_content)
            added += 1
            logging.info("Added: %s", rel)

    zf.close()
    logging.info(
        "Sync complete — %d added, %d updated, %d unchanged",
        added,
        updated,
        unchanged,
    )


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
) -> None:
    """
    Execute a single module (or expand a compound module).

    *processed_ids* tracks module GUIDs that have already run, preventing
    duplicate execution when compound modules reference the same module.

    When *dry_run* is True, commands are logged but not executed.
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
        for source_file in matching_files:
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
            execute_command(exe, cmdline, dest_dir, export_file, append, wait_timeout, debug, dry_run)
    else:
        cmdline = substitute_variables(cmdline_template, base_vars)
        exe = find_executable(executable_name, modules_dir, module_name)
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
        "--msync",
        action="store_true",
        help=(
            "Sync modules from the KapeFiles GitHub repository "
            "(https://github.com/EricZimmerman/KapeFiles) and exit."
        ),
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
    # --msync: download latest modules and exit
    # ------------------------------------------------------------------
    if args.msync:
        modules_dir.mkdir(parents=True, exist_ok=True)
        sync_modules(modules_dir)
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

    for module_name in module_names:
        module_files = find_module_files(modules_dir, module_name)
        if not module_files:
            logging.warning("Module '%s' not found in %s", module_name, modules_dir)
            continue
        for module_file in module_files:
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
                )
            except Exception as exc:  # noqa: BLE001
                logging.error("Error processing module '%s': %s", module_name, exc)
                if args.debug:
                    traceback.print_exc()

    logging.info("Done.")


if __name__ == "__main__":
    main()
