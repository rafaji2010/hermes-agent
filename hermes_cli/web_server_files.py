"""Managed-files policy for the dashboard file browser: root resolution, path containment, entry metadata.
"""

import mimetypes
import os
import shutil
import urllib.parse
import urllib.request
from dataclasses import dataclass
from fastapi import HTTPException, Request
from pathlib import Path
from typing import Any, Dict

from hermes_cli.config import get_hermes_home


_MANAGED_FILES_ROOT_ENV = "HERMES_DASHBOARD_FILES_ROOT"
_HOSTED_MANAGED_FILES_ROOT = Path("/opt/data")


@dataclass(frozen=True)
class ManagedFilesPolicy:
    default_path: Path
    locked_root: Path | None
    can_change_path: bool


def _fs_path(raw_path: str) -> Path:
    raw = str(raw_path or "").strip()
    if not raw:
        raise HTTPException(status_code=400, detail="Path is required")
    if "\0" in raw:
        raise HTTPException(status_code=400, detail="Invalid path")
    try:
        if raw.lower().startswith("file:"):
            parsed = urllib.parse.urlparse(raw)
            if parsed.netloc and parsed.netloc not in {"", "localhost"}:
                raise ValueError
            raw = urllib.request.url2pathname(parsed.path)
        candidate = Path(raw).expanduser()
        if not candidate.is_absolute():
            candidate = Path.cwd() / candidate
        return candidate.resolve(strict=False)
    except (OSError, RuntimeError, ValueError):
        raise HTTPException(status_code=400, detail="Invalid path")


def _canonical_path(path: Path, *, require_exists: bool = False) -> Path:
    try:
        return path.expanduser().resolve(strict=require_exists)
    except FileNotFoundError:
        if require_exists:
            raise HTTPException(status_code=404, detail="Path not found")
        raise
    except (OSError, RuntimeError):
        raise HTTPException(status_code=400, detail="Invalid path")


def _ensure_managed_root(raw_path: str | Path) -> Path:
    root = Path(raw_path).expanduser()
    try:
        root.mkdir(parents=True, exist_ok=True)
        resolved = root.resolve()
    except (OSError, RuntimeError) as exc:
        raise HTTPException(status_code=500, detail=f"Managed files root is unavailable: {exc}")
    if not resolved.is_dir():
        raise HTTPException(status_code=500, detail="Managed files root is not a directory")
    return resolved


def _path_is_under(root: Path, target: Path) -> bool:
    return target == root or root in target.parents


def _path_text(raw_path: str | None) -> str:
    text = str(raw_path or "").strip()
    if "\x00" in text:
        raise HTTPException(status_code=400, detail="Invalid path")
    return text


def _default_hermes_root_is_opt_data() -> bool:
    raw = os.environ.get("HERMES_HOME", "").strip()
    if not raw:
        return False
    try:
        from hermes_constants import get_default_hermes_root

        root = get_default_hermes_root().expanduser().resolve(strict=False)
    except (OSError, RuntimeError):
        root = Path(raw).expanduser().resolve(strict=False)
    return root == _HOSTED_MANAGED_FILES_ROOT


def _dashboard_local_update_managed_externally() -> bool:
    """True when the dashboard should not offer ``hermes update``.

    Containerized dashboards are updated by the outer launcher/image — except a
    ``git`` install (bind-mounted checkout, e.g. the hermes-webui image), where
    the update button is the correct path. pip stays blocked in containers: its
    apply path mutates the running container filesystem.
    """
    from hermes_cli.web_server import PROJECT_ROOT
    from hermes_cli.config import detect_install_method
    if _default_hermes_root_is_opt_data():
        return True
    try:
        from hermes_constants import is_container

        if not is_container():
            return False
    except Exception:
        return False
    try:
        if detect_install_method(PROJECT_ROOT) == "git":
            return False
    except Exception:
        pass
    return True


def _managed_files_policy(request: Request, *, create_root: bool = True) -> ManagedFilesPolicy:
    raw_forced_root = os.environ.get(_MANAGED_FILES_ROOT_ENV, "").strip()
    if raw_forced_root:
        root = _ensure_managed_root(raw_forced_root) if create_root else _canonical_path(Path(raw_forced_root))
        return ManagedFilesPolicy(default_path=root, locked_root=root, can_change_path=False)

    # Remote/OAuth access does not imply a hosted container (a gated macOS launchd
    # install still browses its home). Lock to /opt/data only when the Hermes
    # root actually IS /opt/data or HERMES_DASHBOARD_FILES_ROOT is set.
    if _default_hermes_root_is_opt_data():
        root = _ensure_managed_root(_HOSTED_MANAGED_FILES_ROOT) if create_root else _HOSTED_MANAGED_FILES_ROOT
        return ManagedFilesPolicy(default_path=root, locked_root=root, can_change_path=False)

    home = _canonical_path(Path.home())
    return ManagedFilesPolicy(default_path=home, locked_root=None, can_change_path=True)


def _resolve_managed_path(
    raw_path: str | None, request: Request, *, for_write: bool = False
) -> tuple[ManagedFilesPolicy, Path, str]:
    policy = _managed_files_policy(request)
    text = _path_text(raw_path)
    root = policy.locked_root

    if root is not None and (not text or text in {".", "/"}):
        candidate = root
    elif not text:
        candidate = policy.default_path
    else:
        candidate = Path(text).expanduser()
        if root is not None and not candidate.is_absolute():
            if any(part == ".." for part in candidate.parts):
                raise HTTPException(status_code=400, detail="Path cannot contain '..'")
            candidate = root / candidate
        elif not candidate.is_absolute():
            raise HTTPException(status_code=400, detail="Path must be absolute")

    if ".." in candidate.parts:
        raise HTTPException(status_code=400, detail="Path cannot contain '..'")

    if for_write and not candidate.exists():
        parent = _canonical_path(candidate.parent)
        resolved = parent / candidate.name
    else:
        resolved = _canonical_path(candidate, require_exists=not for_write)

    if root is not None and not _path_is_under(root, resolved):
        raise HTTPException(status_code=403, detail="Path outside managed files root")

    return policy, resolved, str(resolved)


def _managed_response_meta(policy: ManagedFilesPolicy) -> Dict[str, Any]:
    locked_root = str(policy.locked_root) if policy.locked_root is not None else None
    return {"root": locked_root, "locked_root": locked_root, "can_change_path": policy.can_change_path}


def _managed_file_entry(policy: ManagedFilesPolicy, target: Path) -> Dict[str, Any]:
    try:
        resolved = target.resolve()
    except (OSError, RuntimeError):
        raise HTTPException(status_code=400, detail="Invalid path")
    if policy.locked_root is not None and not _path_is_under(policy.locked_root, resolved):
        raise HTTPException(status_code=403, detail="Path outside managed files root")

    try:
        st = resolved.stat()
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Could not stat path: {exc}")

    is_dir = resolved.is_dir()
    mime_type = None if is_dir else (mimetypes.guess_type(resolved.name)[0] or "application/octet-stream")
    return {
        "name": target.name or resolved.name or str(resolved),
        "path": str(resolved),
        "is_directory": is_dir,
        "size": None if is_dir else st.st_size,
        "mtime": st.st_mtime,
        "mime_type": mime_type,
    }
# ---------------------------------------------------------------------------
# Artifacts — generated infographics / diagrams (HTML + PNG) surfaced in the
# dashboard's Artifacts tab. Files live under ``<HERMES_HOME>/artifacts/``;
# future generators publish new ones via :func:`register_artifact`. The
# listing is auth-gated like every other ``/api/*`` route; the file-serve
# route additionally accepts a ``?token=`` query param (see
# ``_QUERY_TOKEN_API_PATHS``) so sandboxed iframes and ``<img>`` tags — plain
# navigations that can't set the session header — can render inline.
# ---------------------------------------------------------------------------


def _artifacts_dir() -> Path:
    """Return (creating on first use) the profile-scoped artifacts directory."""
    artifacts_dir = get_hermes_home() / "artifacts"
    try:
        artifacts_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Could not create artifacts directory: {exc}")
    return artifacts_dir


_ARTIFACT_KIND_BY_SUFFIX: Dict[str, str] = {
    ".png": "image",
    ".jpg": "image",
    ".jpeg": "image",
    ".webp": "image",
    ".gif": "image",
    ".html": "html",
    ".htm": "html",
    ".svg": "svg",
}


def _artifact_kind(path: Path) -> str:
    return _ARTIFACT_KIND_BY_SUFFIX.get(path.suffix.lower(), "file")


def _artifact_entry(target: Path) -> Dict[str, Any]:
    st = target.stat()
    return {
        "name": target.name,
        "path": target.name,
        "kind": _artifact_kind(target),
        "size": st.st_size,
        "mtime": st.st_mtime,
    }


def _resolve_artifact(name: str) -> Path:
    """Resolve an artifact file inside the artifacts dir, 404ing otherwise.

    Path-traversal safe: the candidate is resolved (following symlinks) and
    must still be inside the artifacts directory.
    """
    raw = urllib.parse.unquote(str(name or ""))
    if not raw or "\0" in raw:
        raise HTTPException(status_code=404, detail="Artifact not found")
    artifacts_dir = _artifacts_dir()
    target = (artifacts_dir / raw).resolve()
    if not target.is_relative_to(artifacts_dir.resolve()):
        raise HTTPException(status_code=404, detail="Artifact not found")
    if not target.is_file():
        raise HTTPException(status_code=404, detail="Artifact not found")
    return target

def register_artifact(name: str, source_path) -> Path:
    """Copy a generated artifact into the artifacts dir (not an API route).

    Future artifact generators (infographic exporters, Excalidraw diagrams)
    call this to publish a file into the dashboard-visible artifacts directory.
    ``name`` is the destination filename, ``source_path`` any readable path.
    Returns the destination :class:`pathlib.Path`.
    """
    raw = str(name or "").strip()
    if not raw or raw in {".", ".."} or "/" in raw or "\\" in raw:
        raise ValueError("artifact name must be a plain filename")
    dest = (_artifacts_dir() / raw).resolve()
    source = Path(source_path)
    shutil.copyfile(source, dest)
    return dest
