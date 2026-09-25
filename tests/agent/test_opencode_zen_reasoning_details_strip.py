"""Providers whose relay rejects OpenRouter replay extras must not receive them.

Live repro: a session whose history carried ``reasoning_details`` (written by
earlier OpenRouter turns) died on every opencode-zen call with HTTP 400
``Upstream request failed: ... Extra inputs are not permitted, field:
'messages[N].reasoning_details'`` — the rejected row stays in history, so every
later call died too. The provider profile declares
``supports_reasoning_details=False``; ``build_api_messages`` strips the field
from the wire copy while leaving persisted history and other providers intact.
"""

from agent.turn_context import build_api_messages


class _Agent:
    api_mode = "chat_completions"
    ephemeral_system_prompt = None
    _compression_warning = None
    _current_turn_timestamp = 10_000.0
    model = "deepseek-v4.1-flash"

    def __init__(self, provider):
        self.provider = provider

    @staticmethod
    def _copy_reasoning_content_for_api(_source, _target):
        return None

    @staticmethod
    def _should_sanitize_tool_calls():
        return False


def _history():
    return [
        {"role": "user", "content": "hello"},
        {
            "role": "assistant",
            "content": "hi",
            "reasoning_content": "thought",
            "reasoning_details": [{"type": "reasoning.text", "text": "thought"}],
        },
        {"role": "user", "content": "again"},
    ]


def _send(agent, history):
    request, _ = build_api_messages(
        agent, history, current_turn_user_idx=len(history) - 1,
        ext_prefetch_cache="", plugin_user_context="", moa_config=None, active_system_prompt="",
    )
    return request


def test_opencode_zen_wire_strips_reasoning_details():
    history = _history()
    request = _send(_Agent("opencode-zen"), history)
    assistant = next(m for m in request if m.get("role") == "assistant")
    assert "reasoning_details" not in assistant
    # Persisted history stays untouched — only the wire copy is shaped.
    assert history[1]["reasoning_details"] == [{"type": "reasoning.text", "text": "thought"}]


def test_reasoning_details_kept_for_providers_that_accept_them():
    request = _send(_Agent("openrouter"), _history())
    assistant = next(m for m in request if m.get("role") == "assistant")
    assert assistant["reasoning_details"] == [{"type": "reasoning.text", "text": "thought"}]
