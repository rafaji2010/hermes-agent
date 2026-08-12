"""Tests for the device write guard (SEC P1.2, ADR-013).

Writing to a device path (/dev/zero, /dev/random, block devices, terminals)
can corrupt disks or hang the process — only /dev/null is a legitimate
write target. These tests cover write_file and patch (explicit path and
V4A headers) at the tool boundary, mirroring the read-side device guard.
"""

import json
import unittest
from unittest.mock import patch

from tools.file_tools import (
    _blocked_device_write_error,
    patch_tool,
    write_file_tool,
)


class TestBlockedDeviceWriteError(unittest.TestCase):
    def test_null_is_allowed(self) -> None:
        self.assertIsNone(_blocked_device_write_error("/dev/null"))

    def test_common_devices_are_blocked(self) -> None:
        for path in (
            "/dev/zero",
            "/dev/random",
            "/dev/urandom",
            "/dev/tty",
            "/dev/stdin",
            "/dev/full",
            "/dev/sda",
            "/dev/nvme0n1",
            "/dev/mem",
        ):
            err = _blocked_device_write_error(path)
            self.assertIsNotNone(err, f"{path} should be blocked")
            assert err is not None
            self.assertIn("Refusing to write to a device path", err)

    def test_regular_file_is_allowed(self) -> None:
        self.assertIsNone(_blocked_device_write_error("/tmp/regular-file.txt"))
        self.assertIsNone(_blocked_device_write_error("/home/user/docs/note.md"))

    def test_symlink_hop_to_device_is_blocked(self) -> None:
        # A symlink that resolves into /dev must be caught by the hop walk.
        with patch("os.readlink", side_effect=lambda p: "/dev/zero" if p.endswith("evil-link") else ""):
            self.assertIsNotNone(_blocked_device_write_error("/tmp/evil-link"))


class TestDeviceWriteGuardToolLevel(unittest.TestCase):
    """Tool-level checks: writes to devices are refused before any I/O."""

    def test_write_file_to_device_refused(self) -> None:
        result = write_file_tool("/dev/zero", "boom", task_id="t-dev1")
        payload = json.loads(result)
        self.assertIn("error", payload)
        self.assertIn("Refusing to write to a device path", payload["error"])

    def test_write_file_to_dev_null_passes_guard(self) -> None:
        # /dev/null is the sanctioned discard sink — the device guard must
        # NOT fire on it.  (The ops layer still refuses the write itself,
        # because atomic writes need a writable sibling temp file and /dev
        # isn't writable — that failure is downstream and safe.)
        result = write_file_tool("/dev/null", "discard me", task_id="t-dev2")
        payload = json.loads(result)
        self.assertNotIn("Refusing to write to a device path", payload.get("error", ""))

    def test_patch_replace_to_device_refused(self) -> None:
        result = patch_tool(
            mode="replace", path="/dev/random", old_string="x", new_string="y",
            task_id="t-dev3",
        )
        payload = json.loads(result)
        self.assertIn("error", payload)
        self.assertIn("Refusing to write to a device path", payload["error"])

    def test_patch_v4a_header_to_device_refused(self) -> None:
        # The device path arrives via the V4A patch header (attacker-
        # influenceable), not the explicit path arg.
        v4a = "*** Begin Patch\n*** Update File: /dev/tty\n@@\n-old\n+new\n*** End Patch"
        result = patch_tool(mode="patch", patch=v4a, task_id="t-dev4")
        payload = json.loads(result)
        self.assertIn("error", payload)
        self.assertIn("Refusing to write to a device path", payload["error"])


if __name__ == "__main__":
    unittest.main()
