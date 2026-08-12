"""Tests for the M8.2 gap fixes in the read layer.

Covers the three extensions adopted from the Command Code read_file
findings (ADR-006, D1):

1. D1 §4  — deterministic past-EOF / empty-file recovery notes
            (silence is an expensive tool failure)
2. D1 §20 — CRLF normalization to LF (encoding hygiene)
3. D1 §10 — Unicode (NFC/NFD) filename repair before "not found"

plus the cross-tool relational invariant (D1 §8): a write to a path
must invalidate the read dedup cache so a re-read returns fresh
content, never a stale "unchanged" stub.

Run with:  python -m pytest tests/tools/test_read_file_m82_gaps.py -v
"""

import json
import os
import tempfile
import unittest

from tools.file_tools import read_file_tool, write_file_tool, _read_tracker


def _write(path: str, content: str) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content)


class TestReadFileGapFixes(unittest.TestCase):
    """Real-file behavior tests for the M8.2 read-layer gap fixes."""

    def setUp(self) -> None:
        self._tmp = tempfile.mkdtemp(prefix="m82-gaps-")
        _read_tracker.clear()

    def tearDown(self) -> None:
        _read_tracker.clear()

    def _read(self, path: str, offset: int = 1, task_id: str = "t-eof") -> dict:
        return json.loads(read_file_tool(path, offset=offset, task_id=task_id))

    # ── D1 §4: deterministic recovery notes ────────────────────────────

    def test_past_eof_returns_recovery_hint(self) -> None:
        p = os.path.join(self._tmp, "three.txt")
        _write(p, "one\ntwo\nthree\n")
        result = self._read(p, offset=10)
        self.assertEqual(result["total_lines"], 3)
        self.assertIn("beyond the end of the file", result["hint"])
        self.assertIn("Retry with offset <= 3", result["hint"])

    def test_empty_file_returns_explicit_note(self) -> None:
        p = os.path.join(self._tmp, "empty.txt")
        _write(p, "")
        result = self._read(p)
        self.assertEqual(result["total_lines"], 0)
        self.assertEqual(result["hint"], "File is empty (0 bytes).")

    def test_in_range_read_has_no_recovery_note(self) -> None:
        p = os.path.join(self._tmp, "three.txt")
        _write(p, "one\ntwo\nthree\n")
        result = self._read(p)
        self.assertNotIn("hint", result)
        self.assertEqual(result["content"], "1|one\n2|two\n3|three")

    # ── D1 §20: encoding hygiene ───────────────────────────────────────

    def test_crlf_normalized_to_lf(self) -> None:
        p = os.path.join(self._tmp, "crlf.txt")
        _write(p, "alpha\r\nbeta\r\ngamma\r\n")
        result = self._read(p)
        self.assertNotIn("\r", result["content"])
        self.assertIn("1|alpha\n2|beta\n3|gamma", result["content"])

    def test_lf_file_untouched(self) -> None:
        p = os.path.join(self._tmp, "lf.txt")
        _write(p, "one\ntwo\n")
        result = self._read(p)
        self.assertEqual(result["content"], "1|one\n2|two")

    # ── D1 §10: Unicode filename repair ────────────────────────────────

    def test_nfd_read_of_nfc_file_succeeds(self) -> None:
        p = os.path.join(self._tmp, "café.txt")  # NFC (é as one codepoint)
        _write(p, "espresso\n")
        nfd = os.path.join(self._tmp, "cafe\u0301.txt")  # NFD (e + combining)
        result = self._read(nfd, task_id="t-nfc")
        self.assertEqual(result["content"], "1|espresso")

    def test_nfc_read_of_nfd_file_succeeds(self) -> None:
        p = os.path.join(self._tmp, "cafe\u0301.txt")  # NFD on disk
        _write(p, "macchiato\n")
        nfc = os.path.join(self._tmp, "café.txt")  # NFC in the request
        result = self._read(nfc, task_id="t-nfd")
        self.assertEqual(result["content"], "1|macchiato")

    def test_repair_probe_is_bounded_and_missing_file_still_reports(self) -> None:
        # A genuinely missing file must still reach the not-found path
        # (suggestion/error), not hang or crash on normalization probes.
        p = os.path.join(self._tmp, "missing.txt")
        result = self._read(p, task_id="t-missing")
        self.assertIn("error", result)

    # ── D1 §8: relational invariant — write invalidates dedup ──────────

    def test_write_then_read_returns_fresh_content(self) -> None:
        p = os.path.join(self._tmp, "fresh.txt")
        _write(p, "v1\n")
        task = "t-fresh"

        first = self._read(p, task_id=task)
        self.assertIn("v1", first["content"])

        # Identical re-read -> dedup stub (content_returned False)
        second = self._read(p, task_id=task)
        self.assertTrue(second.get("dedup"))
        self.assertFalse(second.get("content_returned", True))

        # Write new content, then read again -> must be fresh, not a stub
        write_file_tool(p, "v2\n", task_id=task)
        third = self._read(p, task_id=task)
        self.assertNotIn("dedup", third)
        self.assertIn("v2", third["content"])

    def test_read_after_external_write_is_not_stale(self) -> None:
        p = os.path.join(self._tmp, "ext.txt")
        _write(p, "a\n")
        task = "t-ext"

        self._read(p, task_id=task)
        # Simulate an external writer (not through the tool layer)
        _write(p, "b\n")
        result = self._read(p, task_id=task)
        self.assertNotIn("dedup", result)
        self.assertIn("b", result["content"])


if __name__ == "__main__":
    unittest.main()
