# File Collector

I built this tool because I needed a reliable way to copy a large number of
specific files as part of my work.

A safe, general-purpose recursive file-copying tool. It can collect any file type
(such as DWG, PDF, DXF, XLSX, or another extension) from any selected source
folder and all of its subfolders. It can either search everywhere below the source
or limit the search to folders matching a custom path pattern.

The `DWG` search under folders ending in `YOAZIM\DWG` is only a convenient default
for the original use case; it is not a requirement. The tool preserves the
relative directory structure in the output folder, copies rather than moves the
files, and never deletes the originals.

## Standalone Windows EXE

I also built a standalone EXE version of the tool. The EXE file is not included
in this repository.

## Interactive menu

On Windows, double-click `Run_File_Collector.bat`. This opens a Command Prompt
window and starts the menu. Alternatively, run the script without parameters:

```powershell
py .\collect_files.py
```

The English menu lets you choose between the standard DWG operation, collecting a
selected extension from anywhere, or entering both a custom extension and folder
pattern. It then asks for the source and destination paths one field at a time.
After every operation it returns to the main menu and stays open until you select
Exit. Enter `0` at a path or custom-value prompt to cancel the current operation.

## Command-line use (optional)

```powershell
py .\collect_files.py --source "D:\Reviews" --destination "D:\Collected_DWG"
```

Preview without copying:

```powershell
py .\collect_files.py --source "D:\Reviews" --destination "D:\Collected_DWG" --dry-run
```

Search for another file type anywhere below the source:

```powershell
py .\collect_files.py --source "D:\Reviews" --destination "D:\Collected_PDF" --extensions .pdf --scan-anywhere
```

Search for several extensions in a different folder pattern:

```powershell
py .\collect_files.py --source "D:\Reviews" --destination "D:\Collected" --extensions .dwg .dxf --path-pattern "DRAWINGS\FINAL"
```

Existing files are skipped by default. Use `--overwrite` only when replacement is
intended. Use `--verify-hash` for SHA-256 verification when maximum assurance is
more important than speed.

Every run writes a text log, a per-file CSV report, and a missing/error CSV report
to the output folder. The tool never deletes or moves source files.

## Tests

```powershell
py -m unittest -v
```

## Repository files

The source files intended for GitHub are:

- `collect_files.py` - application source code.
- `test_collect_files.py` - automated tests.
- `Run_File_Collector.bat` - Windows launcher for the Python version.
- `README.md` - documentation.
- `.gitignore` - rules for excluding generated and local files.

The `.gitignore` excludes local Python environments, cache files, test artifacts,
PyInstaller build folders, packaged executables, temporary folders, and runtime
logs or CSV reports. These files are generated locally and are not required in
the source repository.
