# pykapetargetexec

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
and place the `Modules` directory next to `kape_modules.py`:

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
kape_modules.py --msync [--mpath PATH]
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
| `--msync` | Sync modules from the KapeFiles GitHub repository and exit |
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
