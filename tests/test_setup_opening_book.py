from pathlib import Path
import tempfile
import unittest
import zipfile

from setup_opening_book import extract_selected


class SetupOpeningBookTests(unittest.TestCase):
    def test_extracts_only_opening_material(self):
        with tempfile.TemporaryDirectory() as folder:
            archive = Path(folder) / "book.zip"
            target = Path(folder) / "out"
            with zipfile.ZipFile(archive, "w") as book:
                book.writestr(
                    "repo/Dataset/開局/中炮/sample.pgn", "sample")
                book.writestr(
                    "repo/Dataset/對局/大師對局/以開局分類/飛相局/a.pgn",
                    "sample")
                book.writestr("repo/Dataset/殘局/unused.pgn", "unused")
                book.writestr("repo/LICENSE", "CC BY 4.0")
            count = extract_selected(archive, target)
            self.assertEqual(count, 3)
            self.assertTrue((target / "Dataset" / "開局" /
                             "中炮" / "sample.pgn").is_file())
            self.assertFalse((target / "Dataset" / "殘局" /
                              "unused.pgn").exists())


if __name__ == "__main__":
    unittest.main()
