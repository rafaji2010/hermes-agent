"""Shieldstral local safety guard (M13.2).

A config-gated, policy-adaptive safety classifier that runs *locally* and
scores a command against a natural-language safety policy, producing a
yes/no verdict BEFORE the cloud smart-approval LLM is consulted.

Backends (in preference order, all local):
  1. **Ollama** — the machine's existing local-inference substrate. A
     Shieldstral 3B model is served under a configured tag (default
     ``shieldstral-text``) and queried via the ``/api/generate`` endpoint.

     PITFALL: Shieldstral is multimodal (it ships a CLIP projector), and
     Ollama's auto-generated Modelfile for ``hf.co/.../Shieldstral-1.0-3B-GGUF:Q4_K_M``
     bundles a broken second ``FROM`` blob, crashing llama-server with
     "Failed to load CLIP model". Build a text-only model instead:
     ``ollama show <tag> --modelfile``, keep only the FIRST ``FROM`` blob,
     and ``ollama create shieldstral-text -f <modelfile>``.
  2. **llama.cpp** — a GGUF file (``Shieldstral-1.0-3B-Q4_K_M.gguf``,
     ~2.15 GB, from ``Metabaron6/Shieldstral-1.0-3B-GGUF``) loaded via
     ``llama_cpp`` if installed. Used when Ollama is absent.

Shieldstral (arxiv 2607.25857) frames moderation as binary question-answering:
the model reads ``policy + content`` and emits a single ``yes``/``no`` token
("does this content violate the policy?"). The guard is **off by default**
(``approvals.shieldstral.enabled: false``) and **fails open** — any backend
error returns ``None`` (no verdict) so a broken local model can never block
legitimate work; the deterministic hardline/deny/risk-tier floors remain the
unconditional safety net.

Config (config.yaml ``approvals.shieldstral``):
  enabled: false
  backend: auto          # "auto" | "ollama" | "llamacpp"
  ollama_model: shieldstral:3b
  ollama_url: http://127.0.0.1:11434
  gguf_path: ~/.hermes/models/Shieldstral-1.0-3B-Q4_K_M.gguf
  policy: "..."          # natural-language safety policy; default below
  timeout: 5             # seconds; on timeout the guard returns None (open)
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from typing import Optional

logger = logging.getLogger(__name__)

DEFAULT_POLICY = (
    "A command is unsafe if it exfiltrates secrets, destroys data, modifies "
    "system security boundaries, downloads and executes remote code, or "
    "escalates privileges. Safe commands read files, run builds/tests, and "
    "manage local project files."
)

SHIELDSTRAL_PROMPT_TEMPLATE = (
    "<|policy|>\n{policy}\n<|content|>\n{content}\n"
    "Does the content violate the policy? Answer with exactly 'yes' or 'no'."
)


def _get_shieldstral_config() -> dict:
    """Read the approvals.shieldstral config block (never mutating it)."""
    try:
        from hermes_cli.config import load_config_readonly
        cfg = load_config_readonly()
        return (cfg.get("approvals", {}) or {}).get("shieldstral", {}) or {}
    except Exception as exc:  # pragma: no cover - config read must fail open
        logger.warning("Shieldstral: config read failed (%s) — guard disabled", exc)
        return {}


def _is_enabled(cfg: dict) -> bool:
    return bool(cfg.get("enabled", False))


def _ollama_generate(model: str, url: str, prompt: str, timeout: int) -> Optional[str]:
    """Return the model's text answer via Ollama /api/generate, or None."""
    payload = json.dumps({
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0.0, "num_predict": 8},
    }).encode()
    req = urllib.request.Request(
        f"{url.rstrip('/')}/api/generate",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode())
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
        logger.warning("Shieldstral: ollama generate failed (%s)", exc)
        return None
    return (body.get("response") or "").strip()


def _llamacpp_generate(gguf_path: str, prompt: str) -> Optional[str]:
    """Return the model's text answer via llama_cpp, or None."""
    try:
        from llama_cpp import Llama  # local import — optional dependency
    except ImportError:
        logger.warning("Shieldstral: llama_cpp not installed — cannot use GGUF backend")
        return None
    try:
        llm = Llama(model_path=gguf_path, n_ctx=1024, verbose=False)
        out = llm(prompt, max_tokens=8, temperature=0.0)
        return (out["choices"][0]["text"] or "").strip()
    except Exception as exc:  # pragma: no cover - model load/run errors
        logger.warning("Shieldstral: llama.cpp generate failed (%s)", exc)
        return None


def shieldstral_verdict(command: str, policy: Optional[str] = None) -> Optional[bool]:
    """Score ``command`` against the safety policy.

    Returns ``True`` (violates policy → block), ``False`` (safe), or ``None``
    (no verdict — disabled, backend missing, or error; caller treats None as
    "pass through to the next guard").
    """
    cfg = _get_shieldstral_config()
    if not _is_enabled(cfg):
        return None

    policy_text = policy or cfg.get("policy") or DEFAULT_POLICY
    prompt = SHIELDSTRAL_PROMPT_TEMPLATE.format(policy=policy_text, content=command)
    timeout = int(cfg.get("timeout", 5))

    backend = cfg.get("backend", "auto")
    answer: Optional[str] = None

    if backend in ("auto", "ollama"):
        model = cfg.get("ollama_model", "shieldstral:3b")
        url = cfg.get("ollama_url", "http://127.0.0.1:11434")
        answer = _ollama_generate(model, url, prompt, timeout)
        if answer is None and backend != "ollama":
            backend = "llamacpp"  # fall through to GGUF

    if backend == "llamacpp" or (answer is None and backend == "auto"):
        import os
        gguf_path = os.path.expanduser(
            cfg.get("gguf_path", "~/.hermes/models/Shieldstral-1.0-3B-Q4_K_M.gguf")
        )
        answer = _llamacpp_generate(gguf_path, prompt)

    if answer is None:
        return None

    low = answer.lower()
    if "yes" in low and "no" not in low:
        return True
    if "no" in low and "yes" not in low:
        return False
    # Ambiguous / malformed answer — treat as no verdict (fail open).
    logger.warning("Shieldstral: ambiguous answer %r — no verdict", answer)
    return None
