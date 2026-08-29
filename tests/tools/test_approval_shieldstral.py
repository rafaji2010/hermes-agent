"""Shieldstral guard integration in the approval flow (M13.2).

Covers the decisions made in tools/approval.py around the local
shieldstral_verdict() result:

- True + escalate=False (default) -> immediate hard block, no cloud call.
- True + escalate=True            -> cloud smart-approval LLM is skipped; the
                                     command escalates to the owner prompt.
- False / None                    -> pass through to the cloud smart-approval
                                     LLM exactly as before.
"""

import pytest

import tools.approval as mod
from tools import shieldstral_guard

SMART_COMMAND = "python -c \"print('hello')\""


@pytest.fixture
def smart_flow(monkeypatch):
    """Standard harness matching test_approval.py's smart-mode flow setup."""
    monkeypatch.setenv("HERMES_SESSION_KEY", "test-shieldstral")
    monkeypatch.setenv("HERMES_EXEC_ASK", "1")
    monkeypatch.delenv("HERMES_CRON_SESSION", raising=False)
    monkeypatch.setattr(mod, "_get_approval_config", lambda: {"mode": "smart"})
    monkeypatch.setattr(mod, "_YOLO_MODE_FROZEN", False)
    monkeypatch.setattr(
        "tools.tirith_security.check_command_security",
        lambda _command: {"action": "allow", "findings": [], "summary": ""},
    )
    mod.clear_session("test-shieldstral")
    mod._permanent_approved.clear()


class TestShieldstralHardBlock:
    def test_true_hard_blocks_by_default(self, monkeypatch, smart_flow):
        monkeypatch.setattr(shieldstral_guard, "shieldstral_verdict", lambda command: True)
        monkeypatch.setattr(
            shieldstral_guard,
            "_get_shieldstral_config",
            lambda: {"enabled": True, "escalate": False},
        )

        def _boom(*_a, **_k):
            raise AssertionError("cloud smart-approval LLM must not be called on hard block")

        monkeypatch.setattr(mod, "_smart_approve", _boom)

        result = mod.check_all_command_guards(SMART_COMMAND, "local")

        assert result == {
            "approved": False,
            "message": "Blocked by the local Shieldstral safety guard.",
            "shieldstral": True,
        }


class TestShieldstralEscalate:
    def test_true_escalates_to_owner_prompt_skipping_cloud_llm(self, monkeypatch, smart_flow):
        monkeypatch.setattr(shieldstral_guard, "shieldstral_verdict", lambda command: True)
        monkeypatch.setattr(
            shieldstral_guard,
            "_get_shieldstral_config",
            lambda: {"enabled": True, "escalate": True},
        )

        def _boom(*_a, **_k):
            raise AssertionError("cloud smart-approval LLM must not be called on escalation")

        monkeypatch.setattr(mod, "_smart_approve", _boom)
        # Owner prompt answered "deny" through a selected transport: the
        # result must be the user-denied shape, NOT the guard's hard block.
        monkeypatch.setattr(
            mod,
            "_present_with_selected_transport",
            lambda **_: {"selected": True, "choice": "deny"},
        )

        result = mod.check_all_command_guards(SMART_COMMAND, "local")

        assert result["approved"] is False
        assert result.get("shieldstral") is not True
        msg = result.get("message") or ""
        assert "Shieldstral" not in msg
        assert "BLOCKED" in msg

    def test_escalate_still_no_cloud_call_when_owner_approves(self, monkeypatch, smart_flow):
        monkeypatch.setattr(shieldstral_guard, "shieldstral_verdict", lambda command: True)
        monkeypatch.setattr(
            shieldstral_guard,
            "_get_shieldstral_config",
            lambda: {"enabled": True, "escalate": True},
        )

        def _boom(*_a, **_k):
            raise AssertionError("cloud smart-approval LLM must not be called on escalation")

        monkeypatch.setattr(mod, "_smart_approve", _boom)
        monkeypatch.setattr(
            mod,
            "_present_with_selected_transport",
            lambda **_: {"selected": True, "choice": "session"},
        )

        result = mod.check_all_command_guards(SMART_COMMAND, "local")

        assert result["approved"] is True
        assert result.get("user_approved") is True


class TestShieldstralPassThrough:
    def test_false_passes_through_to_smart_llm(self, monkeypatch, smart_flow):
        monkeypatch.setattr(shieldstral_guard, "shieldstral_verdict", lambda command: False)
        monkeypatch.setattr(shieldstral_guard, "_get_shieldstral_config", lambda: {"enabled": True})
        monkeypatch.setattr(mod, "_smart_approve", lambda *_a: "approve")

        result = mod.check_all_command_guards(SMART_COMMAND, "local")

        assert result["approved"] is True
        assert result["smart_approved"] is True

    def test_none_fails_open_to_smart_llm(self, monkeypatch, smart_flow):
        monkeypatch.setattr(shieldstral_guard, "shieldstral_verdict", lambda command: None)
        monkeypatch.setattr(shieldstral_guard, "_get_shieldstral_config", lambda: {"enabled": True})
        monkeypatch.setattr(mod, "_smart_approve", lambda *_a: "approve")

        result = mod.check_all_command_guards(SMART_COMMAND, "local")

        assert result["approved"] is True
        assert result["smart_approved"] is True