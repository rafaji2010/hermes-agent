"""Read-only durable session authority adapter (U1D-D).

Hermes owns session identity in ``state.db`` (``SessionDB``).  Workspace
authority resolution only READS the minimum durable metadata needed to
answer "which project/Workspace does this execution context belong to":

- durable session row id (== the stored ``session_key``)
- session ``cwd`` / ``git_repo_root`` / ``profile_name``
- ``archived`` status

Namespace rule (U1C invariant): the volatile Desktop runtime session id
(``host.state.activeSessionId``) is NOT in the SessionDB row-id namespace.
This adapter keys ONLY on the durable row id and performs no speculative
lookup across namespaces — an unprovable identity fails closed (``None``).

This adapter NEVER mutates SessionDB: it opens a read-only connection
(schema init skipped), reads one row, and closes deterministically.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from hermes_constants import get_hermes_home  # type: ignore[import-untyped]

_log = logging.getLogger("hermes.plugins.workspace.session")


def read_session_meta(
    session_id: str,
    home: Optional[Path] = None,
) -> Optional[dict]:
    """Return durable session metadata, or ``None`` when unprovable.

    ``None`` results cover: empty/volatile/unknown ids, missing or
    unreadable session rows, and archived sessions — all of which must
    fail closed (never revive stale authority).
    """
    if not session_id:
        return None

    effective_home = Path(home) if home is not None else Path(get_hermes_home())

    try:
        from hermes_state import SessionDB  # type: ignore[import-untyped]

        db = SessionDB(
            db_path=effective_home / "state.db",
            read_only=True,
        )
    except Exception:
        _log.debug("SessionDB unavailable for %s", effective_home, exc_info=True)
        return None

    try:
        row = db.get_session(session_id)
    except Exception:
        _log.debug("Failed to read session %s", session_id, exc_info=True)
        return None
    finally:
        try:
            db.close()
        except Exception:
            pass

    if not row:
        return None

    # Archived sessions are historical, not authoritative.
    if bool(row.get("archived")):
        _log.debug("Session %s is archived; not authoritative", session_id)
        return None

    return row
