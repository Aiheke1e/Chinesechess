import gzip
import json
from pathlib import Path
import tempfile
import unittest

from opening_library import OpeningLibrary, normalize_notation
from import_ccpd import parse_pgn


class OpeningLibraryTests(unittest.TestCase):
    def test_normalizes_traditional_full_width_notation(self):
        self.assertEqual(normalize_notation("黑方 馬８進７"), "马8进7")
        self.assertEqual(normalize_notation("紅方 車一平二"), "车一平二")

    def test_longest_prefix_lookup(self):
        entries = {
            "炮二平五|马8进7|马二进三|马2进3": {
                "name": "中炮局", "games": 20},
            "炮二平五|马8进7|马二进三|马2进3|车一平二": {
                "name": "中炮对屏风马", "games": 8},
        }
        library = OpeningLibrary(entries, {"max_plies": 24})
        found = library.lookup([
            "红方 炮二平五", "黑方 马8进7", "红方 马二进三",
            "黑方 马2进3", "红方 车一平二", "黑方 车9平8"])
        self.assertEqual(found["name"], "中炮对屏风马")
        self.assertEqual(found["matched_plies"], 5)

    def test_parses_big5_ccpd_pgn(self):
        source = ('[Event "測試棋譜"]\n[ECCO "C40"]\n\n'
                  '1. 炮二平五 馬８進７\n2. 馬二進三 馬２進３\n1-0\n')
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "sample.pgn"
            path.write_bytes(source.encode("cp950"))
            headers, moves = parse_pgn(path)
        self.assertEqual(headers["ECCO"], "C40")
        self.assertEqual(moves, ["炮二平五", "马8进7", "马二进三", "马2进3"])


if __name__ == "__main__":
    unittest.main()
