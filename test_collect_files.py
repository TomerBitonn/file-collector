import csv
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from collect_files import (
    copy_one,
    create_reports,
    normalize_extensions,
    parse_path_pattern,
    run_interactive,
    scan_source,
)


class FileCollectorTests(unittest.TestCase):
    def setUp(self):
        # Keep test data inside the workspace so the suite also runs in
        # restricted/sandboxed environments.
        self.temp_dir = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.root = Path(self.temp_dir.name)
        self.source = self.root / "Reviews"
        self.destination = self.root / "Output"
        self.source.mkdir()
        self.destination.mkdir()
        self.extensions = normalize_extensions(["dwg"])
        self.pattern = parse_path_pattern(r"YOAZIM\DWG")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_scans_matching_folder_and_preserves_relative_path(self):
        wanted = self.source / "Project A" / "Stage 1" / "YOAZIM" / "DWG" / "Plan.DWG"
        unwanted = self.source / "Project A" / "Other" / "ignored.dwg"
        wanted.parent.mkdir(parents=True)
        unwanted.parent.mkdir(parents=True)
        wanted.write_bytes(b"drawing")
        unwanted.write_bytes(b"ignore")

        scan = scan_source(self.source, self.destination, self.extensions, self.pattern, False)

        self.assertEqual([job.source for job in scan.jobs], [wanted])
        expected = self.destination / wanted.relative_to(self.source)
        result = copy_one(scan.jobs[0], overwrite=False, verify_hash=True)
        self.assertEqual(result.status, "COPIED")
        self.assertEqual(expected.read_bytes(), b"drawing")
        self.assertTrue(wanted.exists(), "The source must never be moved or deleted")

    def test_existing_file_is_not_overwritten(self):
        source_file = self.source / "P" / "YOAZIM" / "DWG" / "same.dwg"
        destination_file = self.destination / source_file.relative_to(self.source)
        source_file.parent.mkdir(parents=True)
        destination_file.parent.mkdir(parents=True)
        source_file.write_bytes(b"new")
        destination_file.write_bytes(b"existing")

        scan = scan_source(self.source, self.destination, self.extensions, self.pattern, False)
        result = copy_one(scan.jobs[0], overwrite=False, verify_hash=False)

        self.assertEqual(result.status, "SKIPPED_EXISTS")
        self.assertEqual(destination_file.read_bytes(), b"existing")

    def test_empty_candidate_folder_appears_in_missing_report(self):
        empty = self.source / "P" / "YOAZIM" / "DWG"
        empty.mkdir(parents=True)
        scan = scan_source(self.source, self.destination, self.extensions, self.pattern, False)

        _, _, missing_csv = create_reports(
            self.destination, "test", self.source, scan, [], False
        )
        with missing_csv.open(encoding="utf-8-sig", newline="") as stream:
            rows = list(csv.DictReader(stream))

        self.assertEqual(rows[0]["status"], "NO_MATCHING_FILES")
        self.assertEqual(Path(rows[0]["path"]), empty)

    def test_scan_anywhere_supports_another_extension(self):
        pdf = self.source / "Changed" / "Structure" / "document.PDF"
        pdf.parent.mkdir(parents=True)
        pdf.write_bytes(b"pdf")

        scan = scan_source(
            self.source,
            self.destination,
            normalize_extensions(["pdf"]),
            self.pattern,
            True,
        )

        self.assertEqual([job.source for job in scan.jobs], [pdf])

    def test_destination_inside_source_is_not_scanned(self):
        destination = self.source / "Collected"
        original = self.source / "P" / "YOAZIM" / "DWG" / "original.dwg"
        already_collected = destination / "Old" / "YOAZIM" / "DWG" / "old.dwg"
        original.parent.mkdir(parents=True)
        already_collected.parent.mkdir(parents=True)
        original.write_bytes(b"original")
        already_collected.write_bytes(b"old")

        scan = scan_source(self.source, destination, self.extensions, self.pattern, False)

        self.assertEqual([job.source for job in scan.jobs], [original])

    def test_no_candidate_folder_is_reported(self):
        scan = scan_source(self.source, self.destination, self.extensions, self.pattern, False)
        _, _, missing_csv = create_reports(
            self.destination, "no_target", self.source, scan, [], False
        )

        with missing_csv.open(encoding="utf-8-sig", newline="") as stream:
            rows = list(csv.DictReader(stream))

        self.assertEqual(rows[0]["status"], "TARGET_FOLDER_NOT_FOUND")
        self.assertEqual(Path(rows[0]["path"]), self.source)

    @patch("builtins.input", side_effect=["4"])
    def test_interactive_menu_can_exit(self, _mock_input):
        self.assertEqual(run_interactive(), 0)

    @patch("collect_files.run_collection", return_value=0)
    @patch(
        "builtins.input",
        side_effect=["1", "source", "destination", "", "", "", "", "4"],
    )
    def test_interactive_standard_choice_returns_to_menu(
        self, _mock_input, mocked_run_collection
    ):
        self.assertEqual(run_interactive(), 0)
        mocked_run_collection.assert_called_once()
        args, kwargs = mocked_run_collection.call_args
        self.assertEqual(args[0], Path("source"))
        self.assertEqual(args[1], Path("destination"))
        self.assertEqual(args[2], [".dwg"])
        self.assertEqual(args[3], "YOAZIM/DWG")
        self.assertFalse(kwargs["scan_anywhere"])
        self.assertFalse(kwargs["overwrite"])
        self.assertFalse(kwargs["verify_hash"])
        self.assertFalse(kwargs["dry_run"])


if __name__ == "__main__":
    unittest.main()
