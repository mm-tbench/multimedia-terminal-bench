"""Pin the brace-repair behavior of ``recover_text_mode_tool_calls``.

These tests are intentionally pure-stdlib — no harbor / litellm dependencies —
so they can run with just ``pytest tests/test_tool_call_recovery.py`` from the
repo root.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

# Load ``_tool_call_recovery`` directly via its file path so we don't trigger
# ``mmtb_runtime/agent/__init__.py``, which imports the agent classes that
# depend on litellm/harbor. The recovery helper itself is pure stdlib, so this
# keeps the test runnable with just ``pytest`` and no extras.
_RECOVERY_PATH = (
    Path(__file__).resolve().parent.parent
    / "mmtb_runtime"
    / "agent"
    / "_tool_call_recovery.py"
)
_spec = importlib.util.spec_from_file_location(
    "_tool_call_recovery_under_test", _RECOVERY_PATH
)
assert _spec is not None and _spec.loader is not None
_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_module)
recover_text_mode_tool_calls = _module.recover_text_mode_tool_calls
normalise_commands_arg = _module.normalise_commands_arg


# Real captured trace from a Qwen3-Omni-30B-A3B run on
# ``audience-ringtone-detection``. The JSON inside ``<tool_call>...</tool_call>``
# has a brace deficit (3 opens, 2 closes) which is what triggered the original
# bug.
MALFORMED_HERMES_BLOCK = (
    "Content: It seems that the recordings directory might not exist or is empty. "
    "Let me check if it exists and create it if necessary, then check for any audio files.\n"
    "\n"
    "<tool_call>\n"
    '{"name": "execute_commands", "arguments": {"analysis": "The recordings directory '
    "doesn't seem to exist. I should create it and see if there are any audio files in "
    'the parent directory.", "plan": "Create the recordings directory and check what '
    'files exist in the current directory.", "commands": [{"keystrokes": '
    '"mkdir -p ./recordings\\n"}]}\n'
    "</tool_call>\n"
    "\n"
    "Tool Calls: []\n"
)


def test_recovers_brace_deficit() -> None:
    recovered = recover_text_mode_tool_calls(MALFORMED_HERMES_BLOCK)
    assert len(recovered) == 1

    call = recovered[0]
    assert call["function"]["name"] == "execute_commands"

    args = json.loads(call["function"]["arguments"])
    assert isinstance(args, dict)
    assert set(args.keys()) >= {"analysis", "plan", "commands"}
    assert args["commands"][0]["keystrokes"].startswith("mkdir -p")


def test_recovered_call_id_prefix() -> None:
    recovered = recover_text_mode_tool_calls(MALFORMED_HERMES_BLOCK)
    assert recovered, "expected at least one recovered call"
    assert recovered[0]["id"].startswith("text-mode-tc-")


def test_returns_empty_for_clean_content() -> None:
    content = "Just a normal assistant message with no tool call markup."
    assert recover_text_mode_tool_calls(content) == []


def test_returns_empty_for_unrepairable_payload() -> None:
    content = "<tool_call>{not json at all}</tool_call>"
    # Helper must NOT raise — silently drops unparseable blocks.
    assert recover_text_mode_tool_calls(content) == []


def test_handles_well_formed_block() -> None:
    content = '<tool_call>{"name": "x", "arguments": {"k": "v"}}</tool_call>'
    recovered = recover_text_mode_tool_calls(content)
    assert len(recovered) == 1
    assert recovered[0]["function"]["name"] == "x"
    assert json.loads(recovered[0]["function"]["arguments"]) == {"k": "v"}


# ------------------------------------------------------------------ #
# normalise_commands_arg — Qwen3-Omni emits ``commands`` as either a #
# bare string, a list of strings, or a list of dicts. The helper     #
# normalises all three shapes into a list of (keystrokes, duration). #
# ------------------------------------------------------------------ #


def test_normalise_commands_string_singleton():
    out = normalise_commands_arg("ls -la\n")
    assert out == [("ls -la\n", 1.0)]


def test_normalise_commands_list_of_strings():
    out = normalise_commands_arg(["mkdir foo\n", "ls\n"])
    assert out == [("mkdir foo\n", 1.0), ("ls\n", 1.0)]


def test_normalise_commands_list_of_dicts():
    out = normalise_commands_arg([{"keystrokes": "ls\n", "duration": 2.5}])
    assert out == [("ls\n", 2.5)]


def test_normalise_commands_mixed_list():
    out = normalise_commands_arg(
        ["bare\n", {"keystrokes": "structured\n", "duration": 0.5}]
    )
    assert out == [("bare\n", 1.0), ("structured\n", 0.5)]


def test_normalise_commands_unknown_shape_dropped():
    out = normalise_commands_arg([42, None, "valid\n"])
    assert out == [("valid\n", 1.0)]


def test_normalise_commands_non_list_non_str_returns_empty():
    assert normalise_commands_arg(None) == []
    assert normalise_commands_arg(42) == []
    # dict-as-commands is not list-of-cmd
    assert normalise_commands_arg({"keystrokes": "x"}) == []


def test_normalise_commands_invalid_duration_falls_back():
    out = normalise_commands_arg([{"keystrokes": "x\n", "duration": "not-a-number"}])
    assert out == [("x\n", 1.0)]


def test_truncation_logic_intent():
    """Document the contract we rely on:
    when recovery extracts a tool call from truncated content, the agent does NOT
    raise OutputLengthExceededError. This is enforced in terminus_mm.py's
    _call_llm_with_tools body — we don't import that here (it pulls litellm/harbor),
    but pin the recovery output's non-emptiness behaviour as the input contract."""
    # When recovery succeeds, the test ensures len(...) > 0, which is the value
    # the production guard relies on (`not tool_calls` falsy when non-empty).
    truncated_with_recoverable_call = (
        "<tool_call>\n"
        '{"name": "execute_commands", "arguments": {"analysis": "...", '
        '"plan": "...", "commands": [{"keystrokes": "ls\\n"}]}\n'
        "</tool_call>"
    )
    out = recover_text_mode_tool_calls(truncated_with_recoverable_call)
    assert len(out) >= 1, (
        "recovery must yield at least one call for the soft-guard contract"
    )
    assert out[0]["function"]["name"] == "execute_commands"
