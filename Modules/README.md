# Modules Directory

Place KAPE `.mkape` module files here (preserving their sub-directory structure).

Download the official community module collection from:
  https://github.com/EricZimmerman/KapeFiles/tree/master/Modules

Recommended layout after download:

```
Modules/
├── Apps/
├── Compound/
├── EZTools/
├── KapeResearch/
├── Windows/
├── bin/           ← place module executables here
└── !Disabled/     ← modules placed here are skipped
```

### Placing executables

Each `.mkape` file specifies an `Executable` field.  Before running a module,
place the corresponding binary in one of:

1. `Modules/<ModuleName>/<executable>` — module-specific binary
2. `Modules/bin/<executable>`           — shared binary directory

KAPE does not download binaries automatically; you must obtain and place them
yourself.
