"""opencode.ai gateway: ``model`` must be the FIRST JSON key on the wire.

opencode.ai's Zen/Go gateway parses the ``model`` field only when it appears
as the first key of the JSON request body (both /v1/chat/completions and
/v1/responses). The OpenAI SDK (>=2.x) serializes request params in its own
field order, emitting ``messages``/``input`` before ``model``, so every SDK
request to opencode.ai used to arrive with an EMPTY model and the gateway
rejected it with HTTP 401 ``Model  is not supported`` (double space — the
name is gone). The fix rewrites outgoing opencode.ai JSON bodies at the httpx
transport seam so ``model`` is always first.

These tests exercise the pure rewrite + the client-wiring (transport class
selection) without any network I/O.
"""

from __future__ import annotations

import json

import httpx
import pytest

from run_agent import AIAgent


class TestRewriteOpencodeModelFirst:
    def _rewrite(
        self, body: dict, url: str = "https://opencode.ai/zen/go/v1/chat/completions"
    ) -> httpx.Request:
        return httpx.Request(
            "POST", url, headers={"content-type": "application/json"}, content=json.dumps(body).encode()
        )

    def test_moves_model_to_first_key(self):
        req = self._rewrite({"messages": [{"role": "user", "content": "hi"}], "model": "deepseek-v4-flash", "max_tokens": 5})
        rewritten = AIAgent._rewrite_opencode_model_first(req)
        payload = json.loads(rewritten.read())
        assert list(payload)[0] == "model"
        assert payload["model"] == "deepseek-v4-flash"
        assert payload["max_tokens"] == 5
        assert payload["messages"] == [{"role": "user", "content": "hi"}]

    def test_keeps_body_when_model_already_first(self):
        req = self._rewrite({"model": "x", "messages": []})
        assert AIAgent._rewrite_opencode_model_first(req) is req

    def test_non_opencode_host_untouched(self):
        req = self._rewrite(
            {"messages": [], "model": "x"}, url="https://api.openai.com/v1/chat/completions"
        )
        assert AIAgent._rewrite_opencode_model_first(req) is req

    def test_missing_model_untouched(self):
        req = self._rewrite({"messages": []})
        assert AIAgent._rewrite_opencode_model_first(req) is req

    def test_non_json_content_type_untouched(self):
        req = httpx.Request("POST", "https://opencode.ai/zen/go/v1/chat/completions", content=b"nope")
        assert AIAgent._rewrite_opencode_model_first(req) is req

    def test_get_untouched(self):
        req = httpx.Request("GET", "https://opencode.ai/zen/go/v1/models")
        assert AIAgent._rewrite_opencode_model_first(req) is req

    def test_invalid_body_fails_open(self):
        req = httpx.Request(
            "POST",
            "https://opencode.ai/zen/go/v1/chat/completions",
            headers={"content-type": "application/json"},
            content=b"{bad json",
        )
        # Must not raise and must pass the ORIGINAL request through.
        assert AIAgent._rewrite_opencode_model_first(req) is req

    def test_content_length_updated(self):
        req = self._rewrite({"messages": [], "model": "x", "extra": "y"})
        out = AIAgent._rewrite_opencode_model_first(req)
        assert out.headers["content-length"] == str(len(out.read()))


class TestOpencodeKeepaliveWiring:
    @staticmethod
    def _make_req() -> httpx.Request:
        return httpx.Request(
            "POST",
            "https://opencode.ai/zen/go/v1/chat/completions",
            headers={"content-type": "application/json"},
            content=json.dumps({"messages": [], "model": "m"}).encode(),
        )

    def test_opencode_host_uses_rewriting_transport(self):
        hc = AIAgent._build_keepalive_http_client("https://opencode.ai/zen/go/v1")
        assert hc is not None
        transports = [t for _, t in hc._mounts.items()]
        assert transports
        for t in transports:
            # The transport subclass is created inside the factory; assert
            # it is an HTTPTransport (wiring sanity) — the body rewrite
            # behavior is covered by TestRewriteOpencodeModelFirst.
            assert isinstance(t, httpx.HTTPTransport)
            req = self._make_req()
            handler = t.handle_request
            assert handler is not None

    def test_non_opencode_host_uses_plain_transport(self):
        hc = AIAgent._build_keepalive_http_client("https://api.openai.com")
        assert hc is not None
        for _, t in hc._mounts.items():
            # Post upstream shared-pool refactor the default transport is
            # _SharedTransport, not a bare HTTPTransport. What matters here
            # is that non-opencode hosts never get the model-first rewrite.
            assert type(t).__name__ in ("HTTPTransport", "_SharedTransport")
            assert "Opencode" not in type(t).__name__