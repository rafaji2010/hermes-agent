"""Tests for the dashboard Artifacts API (``/api/artifacts``).

The Artifacts feature surfaces generated infographics / diagrams (HTML + PNG)
from ``<HERMES_HOME>/artifacts/`` in the dashboard's Artifacts tab. These
tests exercise the real FastAPI routes against a per-test temp ``HERMES_HOME``
via Starlette's ``TestClient``.
"""

from pathlib import Path

import pytest

from hermes_constants import get_hermes_home


@pytest.fixture(autouse=True)
def _web_server_client(_isolate_hermes_home):
    try:
        from starlette.testclient import TestClient
    except ImportError:
        pytest.skip("fastapi/starlette not installed")

    from hermes_cli.web_server import (
        app,
        _SESSION_HEADER_NAME,
        _SESSION_TOKEN,
    )

    client = TestClient(app)
    client.headers[_SESSION_HEADER_NAME] = _SESSION_TOKEN
    return client


@pytest.fixture
def artifacts_dir() -> Path:
    artifacts_dir = get_hermes_home() / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    return artifacts_dir


def _write(name: str, data: bytes | str, artifacts_dir: Path) -> Path:
    target = artifacts_dir / name
    if isinstance(data, str):
        target.write_text(data)
    else:
        target.write_bytes(data)
    return target


class TestListArtifacts:
    def test_list_returns_entries_with_kind_size_mtime(self, _web_server_client, artifacts_dir):
        png = _write("fleet.png", b"\x89PNG\r\n\x1a\nfake", artifacts_dir)
        html = _write("fleet.html", "<html>fleet</html>", artifacts_dir)
        _write("notes.txt", "just some text", artifacts_dir)

        resp = _web_server_client.get("/api/artifacts")

        assert resp.status_code == 200
        body = resp.json()
        assert set(body.keys()) == {"artifacts"}
        by_name = {entry["name"]: entry for entry in body["artifacts"]}

        assert set(by_name) == {"fleet.png", "fleet.html", "notes.txt"}

        png_entry = by_name["fleet.png"]
        assert png_entry["kind"] == "image"
        assert png_entry["path"] == "fleet.png"
        assert png_entry["size"] == png.stat().st_size
        assert png_entry["mtime"] == pytest.approx(png.stat().st_mtime)

        assert by_name["fleet.html"]["kind"] == "html"
        assert by_name["notes.txt"]["kind"] == "file"

    def test_list_ignores_directories(self, _web_server_client, artifacts_dir):
        (artifacts_dir / "subdir").mkdir()

        resp = _web_server_client.get("/api/artifacts")

        assert resp.status_code == 200
        assert resp.json()["artifacts"] == []

    def test_requires_auth(self, _web_server_client, artifacts_dir):
        _write("fleet.html", "<html>x</html>", artifacts_dir)
        _web_server_client.headers.pop("X-Hermes-Session-Token", None)

        resp = _web_server_client.get("/api/artifacts")

        assert resp.status_code == 401


class TestServeArtifact:
    def test_html_kind_serves_text_html(self, _web_server_client, artifacts_dir):
        _write("fleet.html", "<html><body>fleet</body></html>", artifacts_dir)

        resp = _web_server_client.get("/api/artifacts/fleet.html")

        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/html")
        assert b"<html><body>fleet</body></html>" in resp.content

    def test_image_kind_serves_bytes(self, _web_server_client, artifacts_dir):
        _write("fleet.png", b"\x89PNG\r\n\x1a\nfake-bytes", artifacts_dir)

        resp = _web_server_client.get("/api/artifacts/fleet.png")

        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("image/png")
        assert resp.content == b"\x89PNG\r\n\x1a\nfake-bytes"

    def test_svg_kind_serves_image_svg_xml(self, _web_server_client, artifacts_dir):
        _write("fleet.svg", "<svg xmlns='http://www.w3.org/2000/svg'/>", artifacts_dir)

        resp = _web_server_client.get("/api/artifacts/fleet.svg")

        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("image/svg+xml")

    def test_missing_artifact_404(self, _web_server_client, artifacts_dir):
        resp = _web_server_client.get("/api/artifacts/nope.png")
        assert resp.status_code == 404

    def test_query_token_authenticates_inline_render(self, _web_server_client, artifacts_dir):
        """Iframes/img tags can't set the session header; the file route must
        accept the loopback ``?token=`` query param like ``/api/files/download``."""
        from hermes_cli.web_server import _SESSION_TOKEN

        _write("fleet.html", "<html>inline</html>", artifacts_dir)

        authed = _web_server_client.get(
            f"/api/artifacts/fleet.html?token={_SESSION_TOKEN}"
        )
        assert authed.status_code == 200

        _web_server_client.headers.pop("X-Hermes-Session-Token", None)
        unauth = _web_server_client.get("/api/artifacts/fleet.html")
        assert unauth.status_code == 401


class TestPathTraversal:
    def test_dotdot_escape_is_404(self, _web_server_client, artifacts_dir, tmp_path):
        secret = tmp_path / "secret.txt"
        secret.write_text("sensitive")

        for attempt in (
            "../secret.txt",
            "..%2Fsecret.txt",
            "..%2fsecret.txt",
            "nested/../../secret.txt",
            "%2e%2e/secret.txt",
        ):
            resp = _web_server_client.get(f"/api/artifacts/{attempt}")
            assert resp.status_code == 404, attempt
            assert "sensitive" not in resp.text

    def test_absolute_path_escape_is_404(self, _web_server_client, artifacts_dir):
        resp = _web_server_client.get("/api/artifacts//etc/passwd")
        assert resp.status_code == 404

    def test_symlink_escaping_dir_is_404(self, _web_server_client, artifacts_dir, tmp_path):
        secret = tmp_path / "secret.txt"
        secret.write_text("sensitive")
        (artifacts_dir / "evil.png").symlink_to(secret)

        resp = _web_server_client.get("/api/artifacts/evil.png")

        assert resp.status_code == 404


class TestRegisterArtifact:
    def test_copies_source_into_artifacts_dir(self, _isolate_hermes_home, tmp_path):
        from hermes_cli.web_server import register_artifact

        source = tmp_path / "generated.png"
        source.write_bytes(b"\x89PNG\r\n\x1a\nregistered")

        dest = register_artifact("registered.png", source)

        assert dest == get_hermes_home() / "artifacts" / "registered.png"
        assert dest.read_bytes() == b"\x89PNG\r\n\x1a\nregistered"

    def test_rejects_escaping_name(self, _isolate_hermes_home, tmp_path):
        from hermes_cli.web_server import register_artifact

        source = tmp_path / "generated.png"
        source.write_bytes(b"x")

        with pytest.raises(ValueError):
            register_artifact("../escape.png", source)
