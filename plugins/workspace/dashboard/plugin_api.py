"""Workspace Plugin — Dashboard REST API

Exposes the FastAPI router at ``/api/plugins/workspace/``.

Endpoints:
    GET  /health               Health check (M0 scaffold)
    GET  /v1/health            v1 health check with DB status
    GET  /v1/workspaces        List workspaces
    POST /v1/workspaces        Create workspace
    GET  /v1/repositories      List repositories
    POST /v1/repositories      Register repository

The v1 routes are defined in ``backend/api/v1.py`` and included here
so they mount under ``/api/plugins/workspace/v1/``.

Imported and mounted by ``_mount_plugin_api_routes()`` in
``hermes_cli/web_server.py`` when the web server starts.

Import strategy
---------------
Hermes loads dashboard plugin API files via::

    importlib.util.spec_from_file_location("hermes_dashboard_plugin_<name>", path)

which registers the module as a *flat* name, not as part of a Python
package hierarchy.  Relative imports (``from ..backend.api.v1``) fail
with ``ImportError: attempted relative import with no known parent
package`` at runtime, even though they work in test suites that run
from the repo root.

The workaround: try the relative import first (happy path in tests),
and on failure add the plugin's root directory to ``sys.path`` so
``backend`` resolves as a top-level absolute import.
"""

from __future__ import annotations

import os
import sys

from fastapi import APIRouter

__all__ = ["router"]

# ---------------------------------------------------------------------------
# Import the v1 router — works under both package and flat-module loading.
# ---------------------------------------------------------------------------

v1_router = None

try:
    from ..backend.api.v1 import router as rt  # type: ignore[import-untyped]
    v1_router = rt
except ImportError:
    # Loaded as a flat module by Hermes' dashboard plugin loader — resolve
    # the plugin root from __file__ and import backend as a top-level package.
    _plugin_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if _plugin_root not in sys.path:
        sys.path.insert(0, _plugin_root)
    from backend.api.v1 import router as rt  # type: ignore[import-untyped]
    v1_router = rt


# -- router --------------------------------------------------------------------

router = APIRouter()


# -- M0 scaffold health endpoint (kept for backward compat) -------------------


@router.get("/health")
def health():
    """Return the plugin health status."""
    return {
        "status": "ok",
        "plugin": "workspace",
        "version": "0.1.0",
    }


# -- v1 routes -----------------------------------------------------------------

router.include_router(v1_router)
