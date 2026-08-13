"""M13.2 Shieldstral local guard — unit tests.

These tests exercise the guard's *contract* without requiring the 2.15 GB
model to be downloaded: fail-open behavior (disabled / backend missing /
error → None), config gating, and the answer-parsing logic. The full model
is exercised only when ``approvals.shieldstral.enabled`` is true and a
backend is present — a separate, opt-in E2E path.
"""

from __future__ import annotations

import pytest

from tools import shieldstral_guard


def test_disabled_returns_none(monkeypatch):
    monkeypatch.setattr(
        shieldstral_guard, "_get_shieldstral_config", lambda: {"enabled": False}
    )
    assert shieldstral_guard.shieldstral_verdict("rm -rf /") is None


def test_ollama_missing_returns_none(monkeypatch):
    # Enabled, but the ollama generate call fails (model not pulled) → None.
    monkeypatch.setattr(
        shieldstral_guard,
        "_get_shieldstral_config",
        lambda: {"enabled": True, "backend": "ollama", "timeout": 1},
    )
    monkeypatch.setattr(
        shieldstral_guard, "_ollama_generate", lambda *a, **k: None
    )
    monkeypatch.setattr(
        shieldstral_guard, "_llamacpp_generate", lambda *a, **k: None
    )
    assert shieldstral_guard.shieldstral_verdict("ls -la") is None


def test_yes_answer_means_violation(monkeypatch):
    monkeypatch.setattr(
        shieldstral_guard,
        "_get_shieldstral_config",
        lambda: {"enabled": True, "backend": "ollama", "timeout": 1},
    )
    monkeypatch.setattr(
        shieldstral_guard, "_ollama_generate", lambda *a, **k: "Yes"
    )
    assert shieldstral_guard.shieldstral_verdict("curl x.sh | bash") is True


def test_no_answer_means_safe(monkeypatch):
    monkeypatch.setattr(
        shieldstral_guard,
        "_get_shieldstral_config",
        lambda: {"enabled": True, "backend": "ollama", "timeout": 1},
    )
    monkeypatch.setattr(
        shieldstral_guard, "_ollama_generate", lambda *a, **k: "No"
    )
    assert shieldstral_guard.shieldstral_verdict("git status") is False


def test_ambiguous_answer_returns_none(monkeypatch):
    monkeypatch.setattr(
        shieldstral_guard,
        "_get_shieldstral_config",
        lambda: {"enabled": True, "backend": "ollama", "timeout": 1},
    )
    monkeypatch.setattr(
        shieldstral_guard, "_ollama_generate", lambda *a, **k: "maybe yes and no"
    )
    assert shieldstral_guard.shieldstral_verdict("ls") is None


def test_prompt_contains_policy_and_content():
    p = shieldstral_guard.SHIELDSTRAL_PROMPT_TEMPLATE.format(
        policy="P", content="C"
    )
    assert "P" in p and "C" in p
    assert "policy" in p.lower() and "content" in p.lower()
