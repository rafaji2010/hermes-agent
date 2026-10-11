"""Regression: families that answer HTTP 400 to ``thinking: {"type": "disabled"}`` must not be sent it."""
import pytest

from agent.anthropic_adapter import _accepts_thinking_disable


@pytest.mark.parametrize("model", ["claude-fable-5-1", "claude-opus-5-5", "claude-opus-5.5", "claude-sonnet-5-5"])
def test_mandatory_thinking_families_are_not_sent_disabled(model):
    assert not _accepts_thinking_disable(model)


def test_families_that_accept_disable_still_get_it():
    assert _accepts_thinking_disable("claude-opus-4-8")
