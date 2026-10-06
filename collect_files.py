from __future__ import annotations

import argparse
import concurrent.futures
import csv
import hashlib
import logging
import os
import shutil
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, Sequence


APP_NAME = "File Collector"
DEFAULT_PATTERN = ("YOAZIM", "DWG")


@dataclass(frozen=True)
class CopyJob:
    source: Path
    destination: Path


@dataclass(frozen=True)
class CopyResult:
    source: Path
    destination: Path
    status: str
    size: int = 0
    message: str = ""


@dataclass
class ScanResult:
    jobs: list[CopyJob]
    candidate_folders: dict[Path, int]
    scan_errors: list[tuple[str, Path, str]]
    folders_scanned: int = 0


def normalize_extensions(values: Sequence[str]) -> frozenset[str]:
    extensions: set[str] = set()
    for value in values:
        for item in value.split(","):
            item = item.strip().lower()
            if not item:
                continue
            extensions.add(item if item.startswith(".") else f".{item}")
    if not extensions:
        raise ValueError("At least one file extension is required.")
    return frozenset(extensions)


def parse_path_pattern(value: str) -> tuple[str, ...]:
    normalized = value.replace("\\", "/")
    parts = tuple(part for part in normalized.split("/") if part and part != ".")
    if not parts:
        raise ValueError("The path pattern cannot be empty.")
    return parts


def path_ends_with(path: Path, root: Path, pattern: Sequence[str]) -> bool:
    try:
        relative_parts = path.relative_to(root).parts
    except ValueError:
        return False
    if len(relative_parts) < len(pattern):
        return False
    return tuple(part.casefold() for part in relative_parts[-len(pattern) :]) == tuple(
        part.casefold() for part in pattern
    )


def is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def scan_source(
    source: Path,
    destination: Path,
    extensions: frozenset[str],
    pattern: Sequence[str],
    scan_anywhere: bool,
) -> ScanResult:
    result = ScanResult(jobs=[], candidate_folders={}, scan_errors=[])
    destination_inside_source = is_within(destination, source)

    def visit(folder: Path, active_candidate: Path | None = None) -> None:
        if destination_inside_source and is_within(folder, destination):
            return

        result.folders_scanned += 1
        candidate = active_candidate
        if not scan_anywhere and path_ends_with(folder, source, pattern):
            candidate = folder
            result.candidate_folders.setdefault(folder, 0)

        try:
            with os.scandir(folder) as entries:
                for entry in entries:
                    entry_path = Path(entry.path)
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            visit(entry_path, candidate)
                        elif entry.is_file(follow_symlinks=False):
                            if entry_path.suffix.casefold() not in extensions:
                                continue
                            if not scan_anywhere and candidate is None:
                                continue
                            if candidate is not None:
                                result.candidate_folders[candidate] += 1
                            relative_path = entry_path.relative_to(source)
                            result.jobs.append(
                                CopyJob(entry_path, destination / relative_path)
                            )
                    except OSError as exc:
                        result.scan_errors.append(("ENTRY_ERROR", entry_path, str(exc)))
        except (OSError, PermissionError) as exc:
            result.scan_errors.append(("SCAN_ERROR", folder, str(exc)))

    visit(source)
    return result


def sha256(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def copy_one(job: CopyJob, overwrite: bool, verify_hash: bool) -> CopyResult:
    source = job.source
    destination = job.destination

    try:
        source_size = source.stat().st_size
        destination.parent.mkdir(parents=True, exist_ok=True)

        if destination.exists() and not overwrite:
            return CopyResult(source, destination, "SKIPPED_EXISTS", source_size)

        temp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                prefix=f".{destination.name}.",
                suffix=".copying",
                dir=destination.parent,
                delete=False,
            ) as temp_file:
                temp_path = Path(temp_file.name)

            shutil.copy2(source, temp_path)
            copied_size = temp_path.stat().st_size
            if copied_size != source_size:
                raise OSError(
                    f"Size verification failed: source={source_size}, copied={copied_size}"
                )

            if verify_hash and sha256(source) != sha256(temp_path):
                raise OSError("SHA-256 verification failed")

            if overwrite:
                os.replace(temp_path, destination)
                temp_path = None
            else:
                try:
                    os.link(temp_path, destination)
                    temp_path.unlink()
                    temp_path = None
                except FileExistsError:
                    return CopyResult(source, destination, "SKIPPED_EXISTS", source_size)
                except OSError:
                    # Some network/file systems do not support hard links. "xb"
                    # still guarantees that an existing file is never overwritten.
                    try:
                        with temp_path.open("rb") as input_stream, destination.open("xb") as output_stream:
                            shutil.copyfileobj(input_stream, output_stream, length=1024 * 1024)
                        shutil.copystat(temp_path, destination)
                        temp_path.unlink()
                        temp_path = None
                    except FileExistsError:
                        return CopyResult(source, destination, "SKIPPED_EXISTS", source_size)
                    except Exception:
                        # The destination was created by the exclusive "xb" open,
                        # so it is safe to remove if that copy did not complete.
                        destination.unlink(missing_ok=True)
                        raise

            return CopyResult(source, destination, "COPIED", source_size)
        finally:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)
    except Exception as exc:  # Continue processing other independent files.
        return CopyResult(source, destination, "COPY_ERROR", message=str(exc))


def configure_logger(log_path: Path) -> logging.Logger:
    logger = logging.getLogger("file_collector")
    logger.handlers.clear()
    logger.setLevel(logging.INFO)
    handler = logging.FileHandler(log_path, encoding="utf-8-sig")
    handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(message)s"))
    logger.addHandler(handler)
    return logger


def write_csv(path: Path, headers: Sequence[str], rows: Iterable[Sequence[object]]) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.writer(stream)
        writer.writerow(headers)
        writer.writerows(rows)


def create_reports(
    destination: Path,
    run_id: str,
    source: Path,
    scan: ScanResult,
    results: Sequence[CopyResult],
    scan_anywhere: bool,
) -> tuple[Path, Path, Path]:
    log_path = destination / f"copy_{run_id}.log"
    files_csv = destination / f"copy_{run_id}.csv"
    missing_csv = destination / f"missing_{run_id}.csv"
    logger = configure_logger(log_path)

    for item in results:
        level = logging.ERROR if item.status == "COPY_ERROR" else logging.INFO
        logger.log(
            level,
            "%s | %s -> %s%s",
            item.status,
            item.source,
            item.destination,
            f" | {item.message}" if item.message else "",
        )
    for status, path, message in scan.scan_errors:
        logger.error("%s | %s | %s", status, path, message)

    missing_rows: list[tuple[str, str, str]] = []
    if not scan_anywhere:
        if not scan.candidate_folders:
            missing_rows.append(("TARGET_FOLDER_NOT_FOUND", str(source), ""))
        else:
            for folder, count in sorted(scan.candidate_folders.items(), key=lambda item: str(item[0])):
                if count == 0:
                    missing_rows.append(("NO_MATCHING_FILES", str(folder), ""))
    missing_rows.extend((status, str(path), message) for status, path, message in scan.scan_errors)
    for status, path, message in missing_rows:
        logger.warning(
            "%s | %s%s", status, path, f" | {message}" if message else ""
        )

    write_csv(
        files_csv,
        ("status", "source", "destination", "size_bytes", "message"),
        (
            (item.status, str(item.source), str(item.destination), item.size, item.message)
            for item in results
        ),
    )
    write_csv(missing_csv, ("status", "path", "message"), missing_rows)

    copied = sum(item.status == "COPIED" for item in results)
    skipped = sum(item.status == "SKIPPED_EXISTS" for item in results)
    errors = sum(item.status == "COPY_ERROR" for item in results) + len(scan.scan_errors)
    logger.info(
        "SUMMARY | folders=%d | found=%d | copied=%d | skipped=%d | errors=%d",
        scan.folders_scanned,
        len(scan.jobs),
        copied,
        skipped,
        errors,
    )
    for handler in list(logger.handlers):
        handler.close()
        logger.removeHandler(handler)
    return log_path, files_csv, missing_csv


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Recursively find and copy files while preserving their relative paths."
    )
    parser.add_argument("--source", required=True, type=Path, help="Root folder to scan")
    parser.add_argument("--destination", required=True, type=Path, help="Output folder")
    parser.add_argument(
        "--extensions",
        nargs="+",
        default=[".dwg"],
        help="Extensions to copy, e.g. .dwg or .dwg .pdf (default: .dwg)",
    )
    parser.add_argument(
        "--path-pattern",
        default="YOAZIM/DWG",
        help="Required ending of a folder path (default: YOAZIM/DWG)",
    )
    parser.add_argument(
        "--scan-anywhere",
        action="store_true",
        help="Ignore the path pattern and collect matching files from all folders",
    )
    parser.add_argument("--workers", type=int, default=4, help="Parallel copy workers (default: 4)")
    parser.add_argument("--overwrite", action="store_true", help="Replace existing destination files")
    parser.add_argument("--verify-hash", action="store_true", help="Verify copies with SHA-256 (slower)")
    parser.add_argument("--dry-run", action="store_true", help="Scan and report without copying files")
    return parser


def run_collection(
    source_input: Path,
    destination_input: Path,
    extension_values: Sequence[str],
    path_pattern: str,
    scan_anywhere: bool = False,
    workers: int = 4,
    overwrite: bool = False,
    verify_hash: bool = False,
    dry_run: bool = False,
) -> int:
    try:
        source = source_input.expanduser().resolve(strict=True)
        destination = destination_input.expanduser().resolve(strict=False)
        extensions = normalize_extensions(extension_values)
        pattern = parse_path_pattern(path_pattern)
    except (OSError, ValueError) as exc:
        print(f"Error: {exc}")
        return 2

    if not source.is_dir():
        print(f"Error: Source is not a directory: {source}")
        return 2
    if source == destination:
        print("Error: Source and destination must be different folders.")
        return 2
    if workers < 1:
        print("Error: The number of workers must be at least 1.")
        return 2

    try:
        destination.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        print(f"Error: Could not create the destination folder: {exc}")
        return 2
    run_id = datetime.now().strftime("%Y-%m-%d_%H%M%S_%f")

    print(f"Scanning: {source}")
    scan = scan_source(source, destination, extensions, pattern, scan_anywhere)
    print(f"Found {len(scan.jobs)} matching file(s) in {scan.folders_scanned} folder(s).")

    if dry_run:
        results = [
            CopyResult(job.source, job.destination, "DRY_RUN", job.source.stat().st_size)
            for job in scan.jobs
        ]
    else:
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
            results = list(
                executor.map(
                    lambda job: copy_one(job, overwrite, verify_hash),
                    scan.jobs,
                )
            )

    log_path, files_csv, missing_csv = create_reports(
        destination, run_id, source, scan, results, scan_anywhere
    )
    copied = sum(item.status == "COPIED" for item in results)
    skipped = sum(item.status == "SKIPPED_EXISTS" for item in results)
    errors = sum(item.status == "COPY_ERROR" for item in results) + len(scan.scan_errors)

    print(f"Copied: {copied} | Skipped: {skipped} | Errors: {errors}")
    print(f"Reports: {log_path.name}, {files_csv.name}, {missing_csv.name}")
    return 1 if errors else 0


def clean_entered_path(value: str) -> Path:
    return Path(value.strip().strip('"').strip("'"))


def prompt_required(prompt: str) -> str | None:
    while True:
        value = input(prompt).strip()
        if value == "0":
            return None
        if value:
            return value
        print("A value is required. Enter 0 to cancel.")


def prompt_yes_no(prompt: str, default: bool = False) -> bool:
    suffix = " [Y/n]: " if default else " [y/N]: "
    while True:
        value = input(prompt + suffix).strip().casefold()
        if not value:
            return default
        if value in {"y", "yes"}:
            return True
        if value in {"n", "no"}:
            return False
        print("Please enter Y or N.")


def run_interactive() -> int:
    while True:
        print("\n" + "=" * 62)
        print(f"{APP_NAME} - Main Menu")
        print("=" * 62)
        print("1. Copy DWG files from folders ending in YOAZIM\\DWG")
        print("2. Copy a selected file type from all folders")
        print("3. Custom file type and custom folder path pattern")
        print("4. Exit")

        choice = input("\nSelect an option [1-4]: ").strip()
        if choice == "4":
            print("Goodbye.")
            return 0
        if choice not in {"1", "2", "3"}:
            print("Invalid option. Please select 1, 2, 3, or 4.")
            continue

        extensions = [".dwg"]
        path_pattern = "YOAZIM/DWG"
        scan_anywhere = choice == "2"

        if choice in {"2", "3"}:
            extension_input = prompt_required(
                "Enter file extension(s), separated by commas (example: .pdf,.dxf): "
            )
            if extension_input is None:
                continue
            extensions = [extension_input]

        if choice == "3":
            pattern_input = prompt_required(
                "Enter the folder path ending (example: DRAWINGS\\FINAL): "
            )
            if pattern_input is None:
                continue
            path_pattern = pattern_input

        source_input = prompt_required("Enter the source folder path (or 0 to cancel): ")
        if source_input is None:
            continue
        destination_input = prompt_required(
            "Enter the destination folder path (or 0 to cancel): "
        )
        if destination_input is None:
            continue

        dry_run = prompt_yes_no("Preview only without copying files?", default=False)
        overwrite = False
        verify_hash = False
        if not dry_run:
            overwrite = prompt_yes_no(
                "Overwrite files that already exist in the destination?", default=False
            )
            verify_hash = prompt_yes_no(
                "Verify every copied file with SHA-256? (slower)", default=False
            )

        print("\nStarting operation...")
        try:
            run_collection(
                clean_entered_path(source_input),
                clean_entered_path(destination_input),
                extensions,
                path_pattern,
                scan_anywhere=scan_anywhere,
                overwrite=overwrite,
                verify_hash=verify_hash,
                dry_run=dry_run,
            )
        except Exception as exc:
            # Keep the menu open even after an unexpected per-run failure.
            print(f"Unexpected error: {exc}")

        input("\nOperation finished. Press Enter to return to the main menu...")


def main(argv: Sequence[str] | None = None) -> int:
    actual_argv = list(sys.argv[1:] if argv is None else argv)
    if not actual_argv:
        try:
            return run_interactive()
        except (KeyboardInterrupt, EOFError):
            print("\nGoodbye.")
            return 0

    parser = build_parser()
    args = parser.parse_args(actual_argv)
    return run_collection(
        args.source,
        args.destination,
        args.extensions,
        args.path_pattern,
        scan_anywhere=args.scan_anywhere,
        workers=args.workers,
        overwrite=args.overwrite,
        verify_hash=args.verify_hash,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    sys.exit(main())
