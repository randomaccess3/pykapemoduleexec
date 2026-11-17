# pykapemoduleexec

Python runner for KAPE modules — a Python implementation of the
[KAPE](https://ericzimmerman.github.io/KapeDocs/#!index.md) Module component.

KAPE Modules are YAML-based (`.mkape`) configuration files that describe how
to run a program against a collection of files and where to save the output.
This script loads those modules and executes the configured processors,
replacing KAPE's built-in module runner with a portable Python alternative.

> **Note:** This project implements the **Module** component only.  The KAPE
> Target component (artifact collection) is out of scope.

---

## Requirements

- Python 3.9 or newer
- [PyYAML](https://pypi.org/project/PyYAML/)

```bash
pip install -r requirements.txt
```

---

## Getting modules

Download the community KAPE module files from the
[KapeFiles repository](https://github.com/EricZimmerman/KapeFiles/tree/master/Modules)
and place the `Modules` directory next to `kape_modules.py`, or use the
built-in sync command:

```bash
python kape_modules.py --msync
```

```
kape_modules.py
Modules/
├── Apps/
├── Compound/
├── EZTools/
├── Windows/
├── bin/           ← place module executables here
└── Sample/        ← example module included in this repo
```

You can also keep the Modules directory anywhere and point to it with
`--mpath`.

### Comparing with an installed KAPE instance

Use the same installed modules and binaries for both runners. The community
compound module `!EZParser` references the EZTools parsers, including tools
in subdirectories of `Modules/bin`. It is not included in this repository's
sample modules.

```powershell
& 'C:\Forensic_Program_Files\Acquisition\kape\kape.exe' `
    --msource E:\ --mdest G:\comparison\kape-E-csv --module '!EZParser'

python kape_modules.py `
    --msource E:\ --mdest G:\comparison\python-E-csv --module '!EZParser' `
    --mpath C:\Forensic_Program_Files\Acquisition\kape\Modules --no-mflush --debug
```

Repeat with `C:\` and separate destinations to test the live system.
Keep output **outside the source drive** so recursive parsers do not process
earlier runs. Live system artifacts can change between runs; a mounted,
unchanging evidence drive is better for exact comparisons.
FileMask processing uses directory enumeration, like KAPE. Hidden NTFS
metadata streams such as a drive's live `$MFT` are not added simply because
their paths can be opened; collected copies of those files are still matched.
Directory junctions and directory symlinks are not traversed, preventing the
same artifacts from being processed again through an alias. Directory
enumeration failures are logged rather than silently treated as an empty source.

Only one export processor runs per module. Use separate `--mef json` runs
for JSON-capable modules; modules without that format fall back to their
default processor. `ExportFile` captures console text independently of
CSV/JSON exports. Compare parsed records rather than timestamped filenames,
row order, or console logs.

A zero runner exit code does not guarantee every parser succeeded. Check
processor warnings and console errors before accepting an output as a baseline.
Some JSON exports include parser-generated `InternalId` GUIDs that change even
on identical input. Classify these separately from evidence metadata such as
`SourceAccessed`; neither difference is strict record equality.

### Running EZTools natively in WSL

WSL needs its own Python 3.9+ with PyYAML and a **Linux** .NET runtime matching
the installed tools' `.runtimeconfig.json` files. Windows Python and
`dotnet.exe` are not native Linux equivalents.

Run the setup helper **inside WSL** to check the recommended Python 3.13,
.NET 10 runtime, and PyYAML:

```bash
bash setup_wsl.sh
```

It asks before installing missing components. `--check` only checks and
returns nonzero if setup is incomplete; `--yes` accepts installation prompts.
Existing compatible installations are reused rather than reinstalled.
For unattended installs, run `sudo -v` first if elevated package installation
will be needed; `--yes` does not bypass sudo authentication.

Python 3.13 is built from the latest stable 3.13 source release on
**python.org**, without replacing Ubuntu's system Python. Missing compiler
and library dependencies are installed through APT, which may require `sudo`.
PyYAML comes from its official PyPI release in a dedicated virtual environment.
.NET is installed using `sudo apt-get update` followed by
`sudo apt-get install -y aspnetcore-runtime-10.0`; this includes the core
runtime required by the installed EZTools.

Ubuntu 20.04's default repositories do **not** contain that .NET package.
The helper reports APT failures rather than silently switching installers
or adding repositories. A supported Ubuntu release/feed is needed for that
installation path. See Microsoft's
[Ubuntu installation guide](https://learn.microsoft.com/en-us/dotnet/core/install/linux-ubuntu).

Local installations default to
`~/.local/share/pykapemoduleexec` (`--prefix` overrides this).
On success, the helper prints the Python command and `DOTNET_ROOT`/`PATH`
exports to use in the current shell; it does not edit shell startup files.
Use that reported Python path instead of `python3` below if the distribution's
default Python is older than the project requires.

```bash
python3 kape_modules.py \
    --msource /mnt/e --mdest /mnt/g/comparison/wsl-E-csv \
    --module '!EZParser' \
    --mpath /mnt/c/Forensic_Program_Files/Acquisition/kape/Modules \
    --no-mflush --debug
```

The default platform map runs mapped DLLs with `dotnet`. DLL names must
match their actual case on case-sensitive filesystems. Windows-style
executable subdirectories and EZTools path arguments are translated to native
paths, and the original binary directory remains the working directory so
relative maps and batch files can be found.

A mapping is not a guarantee that a tool can parse artifacts on Linux.
In particular, SumECmd and SrumECmd require Windows ESE libraries for parsing.
Their native runs can fail even though `--help` works. Inspect tool errors and
generated files, not just the runner's exit code: processor failures are
currently logged without making the overall runner exit nonzero.

### Syncing modules

Use `--msync` to automatically download the latest module files from the
[KapeFiles GitHub repository](https://github.com/EricZimmerman/KapeFiles):

```bash
python kape_modules.py --msync
```

This downloads the repository archive, extracts the `Modules/` directory and
updates (or creates) your local Modules folder.  New files are added, changed
files are overwritten and files that exist only locally are left untouched.

You can combine `--msync` with `--mpath` to sync to a custom location:

```bash
python kape_modules.py --msync --mpath /path/to/my/Modules
```

### Placing executables

Each `.mkape` file has an `Executable` field.  Place the corresponding binary
in one of these locations **before** running:

1. `Modules/<ModuleName>/<executable>` — module-specific directory
2. `Modules/bin/<executable>` — shared binary directory

---

## Usage

```
kape_modules.py --msource PATH --mdest PATH --module NAMES [options]
kape_modules.py --mlist [--mdetail] [--mpath PATH]
kape_modules.py --msync [URL] [--mpath PATH]
```

### Arguments

| Argument | Description |
|----------|-------------|
| `--msource PATH` | Source directory containing files to process |
| `--mdest PATH` | Destination directory where output is written |
| `--module NAMES` | Comma-separated list of module names (without `.mkape`) |
| `--mef FORMAT` | Override export format (e.g. `csv`, `json`, `xml`) |
| `--mvars VARS` | Module variables as `key:value` pairs (e.g. `a:1,b:2`) |
| `--mpath PATH` | Path to the Modules directory (default: `./Modules`) |
| `--mlist` | List available modules and exit |
| `--mdetail` | Show full module details including binary status (use with `--mlist`) |
| `--dry-run` | Show what would be executed without running commands |
| `--mthreads N` | Number of worker threads for concurrent execution (default: `1`) |
| `--post-process NAMES` | Comma-separated list of modules to run after `--module` modules complete, using `--mdest` as source |
| `--msync [URL]` | Sync modules from the KapeFiles GitHub repository and exit. Optionally provide a URL to a custom fork's zip archive |
| `--platform-map PATH` | Path to a `platform_map.yaml` mapping Windows executables to platform-specific equivalents |
| `--mflush`, `--no-mflush` | Enable/disable clearing `--mdest` before processing (default: enabled) |
| `--debug` | Enable verbose debug output |

### Examples

**Run a single module:**
```bash
python kape_modules.py \
    --msource C:\evidence \
    --mdest   C:\output \
    --module  EvtxECmd
```

**Run multiple modules with a specific export format:**
```bash
python kape_modules.py \
    --msource /mnt/evidence \
    --mdest   /tmp/output \
    --module  EvtxECmd,PECmd \
    --mef     csv
```

**Run modules and then post-process the output:**
```bash
python kape_modules.py \
    --msource /mnt/evidence \
    --mdest   /tmp/output \
    --module  EvtxECmd,PECmd \
    --post-process "TimelineExplorer,ArchiveReport"
```

**Pass custom variables to a module:**
```bash
python kape_modules.py \
    --msource C:\evidence \
    --mdest   C:\output \
    --module  MyCustomModule \
    --mvars   "inputFile:SYSTEM,outputName:registry_out"
```

**List all available modules with detail:**
```bash
python kape_modules.py --mlist --mdetail
```

**Use a custom modules path:**
```bash
python kape_modules.py \
    --mlist \
    --mpath /path/to/my/Modules
```

**Sync modules from the official KapeFiles repository:**
```bash
python kape_modules.py --msync
```

**Sync from a custom fork:**
```bash
python kape_modules.py --msync https://github.com/YourUser/KapeFiles/archive/main.zip
```

---

## Module variables

The following variables are substituted in a processor's `CommandLine` at
runtime:

| Variable | Value |
|----------|-------|
| `%sourceDirectory%` | Full path to `--msource` |
| `%destinationDirectory%` | Full path to the category sub-folder under `--mdest` |
| `%sourceDriveLetter%` | Drive letter of `--msource` (Windows, e.g. `C:`) |
| `%sourceFile%` | Full path to the matched file (requires `FileMask`) |
| `%sourceDirectoryBase%` | Top-level sub-dir of the matched file under `--msource` |
| `%fileName%` | File name of the matched file (requires `FileMask`) |
| `%kapeDirectory%` | Directory containing `kape_modules.py` |
| `%d%` | Current UTC date/time in `yyyyMMddTHHmmss` format |
| `%guid%` | Random GUID |
| `%key%` | Any key supplied via `--mvars key:value` |

---

## KAPE module format reference

See the [KAPE documentation](https://ericzimmerman.github.io/KapeDocs/#!Pages/2.2-Modules.md)
and the [ModuleGuide.guide](https://github.com/EricZimmerman/KapeFiles/blob/master/Modules/ModuleGuide.guide)
for the full `.mkape` specification.
