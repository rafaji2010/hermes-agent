"""Process-level bootstrap helpers for ``run_agent``.

Three concerns, all tied to ``AIAgent`` boot-time / runtime IO setup:

1. **Lazy OpenAI SDK import** — ``_load_openai_cls`` + ``_OpenAIProxy``
   defer the 240ms-ish ``from openai import OpenAI`` cost until first use,
   while preserving ``isinstance(client, OpenAI)`` checks and
   ``patch("run_agent.OpenAI", ...)`` test patterns.

2. **Crash-resistant stdio** — ``_SafeWriter`` wraps stdout/stderr so
   ``OSError: Input/output error`` from broken pipes (systemd, Docker,
   thread teardown races) cannot crash the agent.  ``_install_safe_stdio``
   applies the wrapper.

3. **HTTP proxy resolution** — ``_get_proxy_from_env`` reads
   ``HTTPS_PROXY`` / ``HTTP_PROXY`` / ``ALL_PROXY``;
   ``_get_proxy_for_base_url`` respects ``NO_PROXY`` for the given base URL.
4. **Codex dual-stack resilience** — the synchronous ChatGPT/Codex transport
   races resolved IPv6/IPv4 addresses so a blackholed family cannot exhaust
   the request watchdog before a working address is attempted.

``run_agent`` re-exports every name so existing
``from run_agent import _get_proxy_from_env`` imports keep working
unchanged.
"""

from __future__ import annotations

import errno
import os
import selectors
import socket
import sys
import time
import urllib.request
from typing import Any, Optional

from utils import base_url_hostname, normalize_proxy_url


# Cached at module level so we only pay the OpenAI SDK import cost once
# per process (after the first lazy load).
_OPENAI_CLS_CACHE = None
_HAPPY_EYEBALLS_DELAY_SECONDS = 0.25


def _interleave_addrinfos(addrinfos: list[tuple]) -> list[tuple]:
    """Interleave resolved address families while preserving resolver order."""
    queues: dict[int, list[tuple]] = {}
    family_order: list[int] = []
    seen: set[tuple] = set()
    for addrinfo in addrinfos:
        family, socktype, proto, _canonname, sockaddr = addrinfo
        marker = (family, socktype, proto, sockaddr)
        if marker in seen:
            continue
        seen.add(marker)
        if family not in queues:
            queues[family] = []
            family_order.append(family)
        queues[family].append(addrinfo)

    interleaved: list[tuple] = []
    while any(queues.values()):
        for family in family_order:
            if queues[family]:
                interleaved.append(queues[family].pop(0))
    return interleaved


def _happy_eyeballs_create_connection(
    address: tuple[str, int],
    timeout: Optional[float],
    source_address: Optional[tuple[str, int]] = None,
    socket_options=(),
):
    """Connect using staggered non-blocking attempts across resolved families.

    ``socket.create_connection`` tries every address serially. A host with
    broken-but-advertised IPv6 can therefore consume the full connect timeout
    for each AAAA record before trying a working IPv4 address. This follows the
    Happy Eyeballs shape from RFC 8305: retain resolver preference, interleave
    families, and start the next candidate after a short delay.
    """
    host, port = address
    addrinfos = _interleave_addrinfos(
        socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    )
    if not addrinfos:
        raise OSError(f"getaddrinfo returned no addresses for {host}")

    selector = selectors.DefaultSelector()
    active: set[socket.socket] = set()
    winner = None
    last_error: Optional[OSError] = None
    deadline = None if timeout is None else time.monotonic() + max(timeout, 0.0)
    next_launch = time.monotonic()
    pending = list(addrinfos)
    in_progress = {
        0,
        errno.EINPROGRESS,
        errno.EWOULDBLOCK,
        errno.EALREADY,
        errno.EINTR,
        getattr(errno, "WSAEWOULDBLOCK", 10035),
    }

    def start_attempt(addrinfo):
        family, socktype, proto, _canonname, sockaddr = addrinfo
        candidate = socket.socket(family, socktype, proto)
        try:
            if source_address is not None:
                local_infos = socket.getaddrinfo(
                    source_address[0],
                    source_address[1],
                    family=family,
                    type=socktype,
                )
                if not local_infos:
                    raise OSError(
                        f"getaddrinfo returned no local {family} address for "
                        f"{source_address[0]}"
                    )
                candidate.bind(local_infos[0][4])
            candidate.setblocking(False)
            result = candidate.connect_ex(sockaddr)
            if result == 0 or result == errno.EISCONN:
                return candidate
            if result not in in_progress:
                raise OSError(result, os.strerror(result))
            selector.register(candidate, selectors.EVENT_WRITE)
            active.add(candidate)
            return None
        except Exception:
            candidate.close()
            raise

    try:
        while pending or active:
            now = time.monotonic()
            if deadline is not None and now >= deadline:
                raise socket.timeout("timed out")

            if pending and now >= next_launch:
                addrinfo = pending.pop(0)
                try:
                    winner = start_attempt(addrinfo)
                except OSError as exc:
                    last_error = exc
                    if not active:
                        next_launch = now
                    continue
                if winner is not None:
                    break
                next_launch = now + _HAPPY_EYEBALLS_DELAY_SECONDS

            wait_timeout = None if deadline is None else max(0.0, deadline - now)
            if pending:
                until_launch = max(0.0, next_launch - now)
                wait_timeout = (
                    until_launch
                    if wait_timeout is None
                    else min(wait_timeout, until_launch)
                )

            events = selector.select(wait_timeout)
            for key, _mask in events:
                candidate = key.fileobj
                error_code = candidate.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR)
                selector.unregister(candidate)
                active.discard(candidate)
                if error_code == 0:
                    winner = candidate
                    break
                candidate.close()
                last_error = OSError(error_code, os.strerror(error_code))
            if winner is not None:
                break
            if not active and pending:
                next_launch = time.monotonic()

        if winner is None:
            if last_error is not None:
                raise last_error
            raise OSError(f"Could not connect to {host}:{port}")

        try:
            selector.unregister(winner)
        except Exception:
            pass
        active.discard(winner)
        winner.settimeout(timeout)
        for option in socket_options or ():
            winner.setsockopt(*option)
        winner.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        return winner
    finally:
        for candidate in active:
            try:
                selector.unregister(candidate)
            except Exception:
                pass
            candidate.close()
        selector.close()


class _HappyEyeballsSyncBackend:
    """httpcore sync backend with concurrent IPv6/IPv4 connection fallback."""

    def __init__(self):
        self._fallback = None

    def _default_backend(self):
        if self._fallback is None:
            from httpcore import SyncBackend

            self._fallback = SyncBackend()
        return self._fallback

    def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: Optional[float] = None,
        local_address: Optional[str] = None,
        socket_options=None,
    ):
        from httpcore import ConnectError, ConnectTimeout
        from httpcore._backends.sync import SyncStream

        source_address = None if local_address is None else (local_address, 0)
        try:
            sock = _happy_eyeballs_create_connection(
                (host, port),
                timeout,
                source_address=source_address,
                socket_options=socket_options or (),
            )
        except socket.timeout as exc:
            raise ConnectTimeout(str(exc)) from exc
        except OSError as exc:
            raise ConnectError(str(exc)) from exc
        return SyncStream(sock)

    def connect_unix_socket(self, *args, **kwargs):
        return self._default_backend().connect_unix_socket(*args, **kwargs)

    def sleep(self, seconds: float) -> None:
        self._default_backend().sleep(seconds)


def _uses_codex_cloud_transport(base_url: str) -> bool:
    return (
        base_url_hostname(base_url).lower() == "chatgpt.com"
        and "/backend-api/codex" in str(base_url).lower()
    )


def _enable_happy_eyeballs(transport) -> None:
    """Install the sync racing backend on one httpx transport, if compatible.

    Reaches into httpx/httpcore private attributes (``transport._pool`` /
    ``pool._network_backend``); safe because httpcore is pinned (1.0.x) and
    both lookups are hasattr-guarded — on an incompatible httpcore this
    degrades to the default serial backend instead of crashing.
    """
    pool = getattr(transport, "_pool", None)
    if pool is not None and hasattr(pool, "_network_backend"):
        pool._network_backend = _HappyEyeballsSyncBackend()


def _load_openai_cls() -> type:
    """Import and cache ``openai.OpenAI``."""
    global _OPENAI_CLS_CACHE
    if _OPENAI_CLS_CACHE is None:
        from openai import OpenAI as _cls
        _OPENAI_CLS_CACHE = _cls
    return _OPENAI_CLS_CACHE


class _OpenAIProxy:
    """Module-level proxy that looks like ``openai.OpenAI`` but imports lazily."""

    __slots__ = ()

    def __call__(self, *args, **kwargs):
        return _load_openai_cls()(*args, **kwargs)

    def __instancecheck__(self, obj):
        return isinstance(obj, _load_openai_cls())

    def __repr__(self):
        return "<lazy openai.OpenAI proxy>"


class _SafeWriter:
    """Transparent stdio wrapper that catches OSError/ValueError from broken pipes.

    When hermes-agent runs as a systemd service, Docker container, or headless
    daemon, the stdout/stderr pipe can become unavailable (idle timeout, buffer
    exhaustion, socket reset). Any print() call then raises
    ``OSError: [Errno 5] Input/output error``, which can crash agent setup or
    run_conversation() — especially via double-fault when an except handler
    also tries to print.

    Additionally, when subagents run in ThreadPoolExecutor threads, the shared
    stdout handle can close between thread teardown and cleanup, raising
    ``ValueError: I/O operation on closed file`` instead of OSError.

    This wrapper delegates all writes to the underlying stream and silently
    catches both OSError and ValueError. It is transparent when the wrapped
    stream is healthy.
    """

    __slots__ = ("_inner",)

    def __init__(self, inner):
        object.__setattr__(self, "_inner", inner)

    def write(self, data):
        try:
            return self._inner.write(data)
        except (OSError, ValueError):
            return len(data) if isinstance(data, str) else 0

    def flush(self):
        try:
            self._inner.flush()
        except (OSError, ValueError):
            pass

    def fileno(self):
        return self._inner.fileno()

    def isatty(self):
        try:
            return self._inner.isatty()
        except (OSError, ValueError):
            return False

    def __getattr__(self, name):
        return getattr(self._inner, name)


def _get_proxy_from_env() -> Optional[str]:
    """Read proxy URL from environment variables.

    Checks HTTPS_PROXY, HTTP_PROXY, ALL_PROXY (and lowercase variants) in order.
    Returns the first valid proxy URL found, or None if no proxy is configured.
    """
    for key in ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY",
                "https_proxy", "http_proxy", "all_proxy"):
        value = os.environ.get(key, "").strip()
        if value:
            return normalize_proxy_url(value)
    return None


def _get_proxy_for_base_url(base_url: Optional[str]) -> Optional[str]:
    """Return an env-configured proxy unless NO_PROXY excludes this base URL."""
    proxy = _get_proxy_from_env()
    if not proxy or not base_url:
        return proxy

    host = base_url_hostname(base_url)
    if not host:
        return proxy

    try:
        if urllib.request.proxy_bypass_environment(host):
            return None
    except Exception:
        pass

    return proxy



def _is_opencode_ai_host(base_url: str) -> bool:
    """True when *base_url* is hosted on opencode.ai (Zen / Go / free)."""
    try:
        from urllib.parse import urlparse

        host = (urlparse(str(base_url or "")).netloc or "").lower()
    except Exception:
        return False
    return host == "opencode.ai" or host.endswith(".opencode.ai")


def _rewrite_opencode_model_first(request) -> object:
    """Move the ``model`` key to the FIRST position in an outgoing JSON body.

    opencode.ai's Zen/Go gateway parses the ``model`` field only when it
    is the first key of the JSON payload — on both /v1/chat/completions and
    /v1/responses. The OpenAI SDK (openai>=2.x) serializes request params
    in its own field order, emitting ``messages``/``input`` before
    ``model``, so every SDK request to opencode.ai arrives with an EMPTY
    model and the gateway rejects it with HTTP 401 ``Model  is not
    supported`` (note the double space — the name is gone). Verified live
    against zen/go 2026-08-23: same body, key-order flipped, outcome
    flips deterministically.

    This rewrite is applied at the httpx transport seam (single choke
    point for primary, request-scoped, streaming, and codex clients), so
    the SDK never needs to know about the gateway's parser quirk. Only
    opencode.ai-hosted POSTs with a JSON body carrying a non-empty string
    ``model`` are touched; anything else passes through unchanged. Fails
    open to the original request on any parse error.
    """
    import json as _json

    try:
        method = str(getattr(request, "method", "") or "").upper()
        if method != "POST":
            return request
        url = str(getattr(request, "url", "") or "")
        if not _is_opencode_ai_host(url):
            return request
        from urllib.parse import urlparse

        host = (urlparse(url).netloc or "").lower()
        if host != "opencode.ai" and not host.endswith(".opencode.ai"):
            return request
        ctype = str(request.headers.get("content-type", "") or "").lower()
        if "json" not in ctype:
            return request
        payload = request.read()
        if not payload:
            return request
        data = _json.loads(payload)
        if not isinstance(data, dict):
            return request
        model = data.get("model")
        if not isinstance(model, str) or not model.strip():
            return request
        if next(iter(data)) == "model":
            return request
        reordered = {"model": model}
        reordered.update({k: v for k, v in data.items() if k != "model"})
        new_body = _json.dumps(reordered, ensure_ascii=False).encode("utf-8")
        headers = request.headers.copy()
        headers["content-length"] = str(len(new_body))
        import httpx as _httpx

        return _httpx.Request(
            method=request.method,
            url=str(request.url),
            headers=headers,
            content=new_body,
            extensions=request.extensions,
        )
    except Exception:
        return request


def _build_opencode_model_first_transport(*args, **kwargs):
    """httpx.HTTPTransport that rewrites opencode.ai POST bodies.

    Used in place of the plain transport for opencode.ai hosts (Zen / Go /
    free) so the gateway always receives ``model`` as the first JSON key.
    Accepts the same args as ``httpx.HTTPTransport``, including the
    ``proxy=`` kwarg (httpx 0.28 routes proxying through HTTPTransport
    itself; Client._init_proxy_transport builds one with proxy=).
    """

    import httpx as _httpx

    class _OpencodeModelFirstTransport(_httpx.HTTPTransport):
        def handle_request(self, request):  # noqa: N802
            return super().handle_request(_rewrite_opencode_model_first(request))

    return _OpencodeModelFirstTransport(*args, **kwargs)


def _build_opencode_model_first_async_transport(*args, **kwargs):
    """Async variant for opencode.ai POST rewriting."""

    import httpx as _httpx

    class _OpencodeModelFirstAsyncTransport(_httpx.AsyncHTTPTransport):
        async def handle_async_request(self, request):  # noqa: N802
            return await super().handle_async_request(_rewrite_opencode_model_first(request))

    return _OpencodeModelFirstAsyncTransport(*args, **kwargs)


def build_keepalive_http_client(
    base_url: str = "",
    *,
    async_mode: bool = False,
    verify: Any = True,
) -> Optional[Any]:
    """Build an httpx client for OpenAI SDK calls with env-only proxy policy.

    Uses explicit ``HTTPS_PROXY`` / ``NO_PROXY`` env vars via
    ``_get_proxy_for_base_url``. Plain no-proxy mounts disable httpx's default
    ``trust_env`` proxy path, so macOS system proxy settings from
    ``urllib.request.getproxies()`` (which omit the ExceptionsList) are not
    applied. Mirrors ``AIAgent._build_keepalive_http_client``.

    Connection lifecycle is managed at the HTTP pool layer
    (``keepalive_expiry=20.0`` reaps idle connections before reverse proxies'
    typical 30-60 s timeouts) instead of the former custom
    ``socket_options`` transport, which broke streaming behind reverse
    proxies (#54049, #12952) and stalled TLS handshakes by stripping
    ``TCP_NODELAY``.

    ``verify`` is forwarded to httpx so auxiliary-client calls (compression,
    vision, web_extract, title generation, etc.) honor the same per-provider
    ``ssl_ca_cert`` / ``ssl_verify`` and ``HERMES_CA_BUNDLE`` settings the main
    client uses. It is passed on the client AND on the plain no-proxy mounts
    (a mounted transport owns the SSL context for its scheme).
    """
    try:
        import httpx

        proxy = _get_proxy_for_base_url(base_url)

        limits = httpx.Limits(
            max_keepalive_connections=20,
            max_connections=100,
            keepalive_expiry=20.0,
        )
        # Generous read=None for SSE streaming endpoints.
        timeout = httpx.Timeout(connect=15.0, read=None, write=15.0, pool=10.0)

        transport_cls = httpx.AsyncHTTPTransport if async_mode else httpx.HTTPTransport
        client_cls = httpx.AsyncClient if async_mode else httpx.Client
        mounts = {}
        # opencode.ai's gateway demands ``model`` as the FIRST JSON key; the
        # OpenAI SDK always emits messages/input first. Use the rewriting
        # transport so every SDK request (chat completions, responses,
        # streaming, codex) lands with the model name in the position the
        # gateway will actually read. This branch also covers the proxied
        # case (keeping our mounts over httpx's auto-proxy transport) so the
        # rewrite applies to proxied opencode.ai traffic too. Reuses the
        # run_agent e9a05d2 logic but lives in process_bootstrap so the
        # extracted build_keepalive_http_client keeps the fix after the
        # 2026-08-26 upstream refactor that moved the builder out of
        # run_agent.py.
        _is_opencode = _is_opencode_ai_host(base_url)
        if proxy is None or _is_opencode:
            if _is_opencode:
                if async_mode:
                    http_transport = _build_opencode_model_first_async_transport(verify=verify, proxy=proxy)
                    https_transport = _build_opencode_model_first_async_transport(verify=verify, proxy=proxy)
                else:
                    http_transport = _build_opencode_model_first_transport(verify=verify, proxy=proxy)
                    https_transport = _build_opencode_model_first_transport(verify=verify, proxy=proxy)
            else:
                http_transport = transport_cls(verify=verify)
                https_transport = transport_cls(verify=verify)
            if not async_mode and _uses_codex_cloud_transport(base_url):
                _enable_happy_eyeballs(http_transport)
                _enable_happy_eyeballs(https_transport)
            mounts = {"http://": http_transport, "https://": https_transport}
            if _is_opencode:
                proxy = None  # transports already carry the proxy
        return client_cls(
            limits=limits,
            timeout=timeout,
            proxy=proxy,
            mounts=mounts or None,
            verify=verify,
        )
    except Exception:
        return None


def _install_safe_stdio() -> None:
    """Wrap stdout/stderr so best-effort console output cannot crash the agent."""
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream is not None and not isinstance(stream, _SafeWriter):
            setattr(sys, stream_name, _SafeWriter(stream))


# Module-level proxy instance — drops in for ``openai.OpenAI``.  Imported as
# ``from agent.process_bootstrap import OpenAI`` (or re-exported via
# ``run_agent`` for legacy tests).
OpenAI = _OpenAIProxy()


__all__ = [
    "OpenAI",
    "_OpenAIProxy",
    "_load_openai_cls",
    "_SafeWriter",
    "_install_safe_stdio",
    "_get_proxy_from_env",
    "_get_proxy_for_base_url",
    "build_keepalive_http_client",
]
