"""Host identity collection for Workspace audit events (U1D-F2).

Records only what the host genuinely exposes.  Never invents identity:

* ``profile_home`` — the effective profile home for this runtime.
* ``session_key`` — the host approval/session-key NAMESPACE.  This is NOT
  a human identity and is never treated as the actor.
* ``session_id`` — only when a durable session id is explicitly supplied
  by the caller (never derived from the volatile runtime id).
* ``turn_id`` / ``tool_call_id`` — recorded only when the host exposes a
  public getter (none exists today; left empty).
* ``actor`` — only when the transport supplies one; currently never.

No secrets, prompt contents, or payloads are ever collected here.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict

_log = logging.getLogger("hermes.plugins.workspace.identity")


def collect_host_identity() -> Dict[str, Any]:
    """Return the identity fields currently exposed by the host.

    Safe to call on every authorization decision: resolution is cheap and
    everything is lazily imported.
    """
    identity: Dict[str, Any] = {
        "profile_home": "",
        "session_key": "",
        "session_id": "",
        "turn_id": "",
        "tool_call_id": "",
        "actor": "",
    }

    try:
        from hermes_constants import get_hermes_home  # type: ignore[import-untyped]

        identity["profile_home"] = str(Path(get_hermes_home()).resolve())
    except Exception:
        pass

    try:
        from tools.approval import (  # type: ignore[import-untyped]
            get_current_session_key,
        )

        identity["session_key"] = get_current_session_key()
    except Exception:
        pass

    return identity
