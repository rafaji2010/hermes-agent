"""The 0600 owner-only hardening must not cancel the live writer's POSIX locks.

``_secure_state_db_files`` tightens ``state.db``/``-wal``/``-shm`` to 0600 with
bare open+fchmod+close descriptors.  Closing any descriptor to a file cancels
every POSIX advisory lock this process holds on it (howtocorrupt §2.2) — and
this helper runs again *after* the writer connection is open.  The next sibling
opener's ordinary clean close then finds no live locks, treats itself as the
LAST connection and unlinks the live ``-wal``/``-shm`` generation underneath
the writer; every later open fail-closes as ``DeletedWalGenerationError``
(a deleted generation is still held).

Production fires this at gateway start: the first cron worker (or CLI, or
dashboard pass) that opens and closes right after the gateway's SessionDB open
displaces the gateway's generation, and the guard refuses every other process
for as long as that gateway lives.
"""

import os
import sqlite3
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

import hermes_state
from hermes_state_dbfile import iter_deleted_sqlite_sidecar_holders

_HOLD_SECONDS = 8.0

# A live writer that runs the production hardening helper on itself, then idles.
# Its report (last stdout lines) exposes whether its own sidecar descriptors were
# unlinked underneath it and whether it can still write.
_WRITER = textwrap.dedent(
    """
    import os, sqlite3, sys, time
    sys.path.insert(0, sys.argv[1])
    os.environ.setdefault("HERMES_HOME", sys.argv[3])
    os.environ["HERMES_STATE_DB_GUARD_BYPASS"] = "1"
    from pathlib import Path
    from hermes_state import _secure_state_db_files

    db = sys.argv[2]
    c1 = sqlite3.connect(db, isolation_level=None)
    c1.execute("PRAGMA journal_mode=WAL")
    c1.execute("CREATE TABLE IF NOT EXISTS t (x INTEGER)")
    _secure_state_db_files(Path(db))            # production helper, live writer
    c1.execute("INSERT INTO t VALUES (1)")      # write AFTER the helper
    print("ready", flush=True)
    time.sleep(float(sys.argv[4]))
    sidecars = []
    for fd in os.listdir("/proc/self/fd"):
        try:
            target = os.readlink("/proc/self/fd/" + fd)
        except OSError:
            continue
        if db + "-wal" in target or db + "-shm" in target:
            sidecars.append(target)
    print("sidecars:" + "|".join(sidecars), flush=True)
    try:
        c1.execute("INSERT INTO t VALUES (99)")
        print("write:ok", flush=True)
    except Exception as exc:
        print("write:" + type(exc).__name__ + ":" + str(exc), flush=True)
    """
)


@pytest.mark.skipif(
    not sys.platform.startswith("linux"),
    reason="close-cancels-POSIX-locks unlink fingerprint is Linux-only",
)
def test_writer_generation_survives_sibling_clean_close(tmp_path):
    db = tmp_path / "state.db"
    home = tmp_path / "home"
    home.mkdir()
    repo_root = os.path.dirname(os.path.abspath(hermes_state.__file__))
    proc = subprocess.Popen(
        [sys.executable, "-c", _WRITER, repo_root, str(db), str(home), str(_HOLD_SECONDS)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1,
    )
    out = proc.stdout
    err = proc.stderr
    assert out is not None and err is not None
    try:
        deadline = time.monotonic() + 30
        line = ""
        while time.monotonic() < deadline:
            line = out.readline().strip()
            if line == "ready":
                break
            assert proc.poll() is None, f"writer exited early: {line}\n{err.read()}"
        assert line == "ready", f"writer never became ready: {line}"

        wal = Path(str(db) + "-wal")
        shm = Path(str(db) + "-shm")
        assert wal.exists() and shm.exists()
        generation = (os.stat(wal).st_ino, os.stat(shm).st_ino)

        # A sibling opener — any other process — opens, writes and closes cleanly.
        # It must NOT be able to treat itself as the last connection: the live
        # writer still holds the file.
        conn = sqlite3.connect(str(db), isolation_level=None, timeout=5.0)
        conn.execute("INSERT INTO t VALUES (2)")
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.close()

        assert wal.exists(), "sibling close unlinked the live -wal generation"
        assert shm.exists(), "sibling close unlinked the live -shm generation"
        assert (os.stat(wal).st_ino, os.stat(shm).st_ino) == generation, (
            "sibling close replaced the live WAL generation")
        assert iter_deleted_sqlite_sidecar_holders(db) == [], (
            "the live writer now holds a deleted sidecar generation")

        # The writer's own view: descriptors still name a live generation, and its
        # next write is not refused with DeletedWalGenerationError.
        report = {}
        while True:
            line = out.readline().strip()
            if line.startswith("sidecars:"):
                report["sidecars"] = line[len("sidecars:"):].split("|") if line != "sidecars:" else []
            if line.startswith("write:"):
                report["write"] = line[len("write:"):]
                break
            assert line, f"writer ended before reporting (rc={proc.poll()})\n{err.read()}"
        assert report["sidecars"], "writer lost its sidecar descriptors"
        assert not any("(deleted)" in target for target in report["sidecars"]), (
            f"writer's sidecar descriptors were unlinked: {report['sidecars']!r}")
        assert report["write"] == "ok", f"writer's next write failed: {report['write']!r}"
    finally:
        proc.terminate()
        proc.wait(timeout=15)


def test_secure_helper_never_closes_its_descriptors(tmp_path, monkeypatch):
    """The helper must retain every descriptor it opens: a close cancels this
    process's POSIX locks on the file, which is how the live generation is lost."""
    path = tmp_path / "state.db"
    path.write_bytes(b"")  # main exists; the -wal/-shm targets are covered by the skip path too

    closed: list[int] = []
    real_close = os.close

    def recording_close(fd):
        closed.append(fd)
        return real_close(fd)

    monkeypatch.setattr(os, "close", recording_close)
    try:
        hermes_state._secure_state_db_files(path, create_main=True)
    finally:
        monkeypatch.setattr(os, "close", real_close)

    assert closed == [], f"_secure_state_db_files closed descriptors {closed!r}"
