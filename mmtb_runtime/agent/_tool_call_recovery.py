"""Recover Hermes-format tool calls from raw model text.

Some open-weight models (notably Qwen3-Omni-30B-A3B) emit tool calls in the
Hermes textual format::

    <tool_call>
    {"name": "...", "arguments": {...}}
    </tool_call>

…but occasionally produce JSON with an off-by-one closing-brace error inside
the block. vLLM's structured tool-call parser correctly refuses to parse the
malformed JSON and returns ``tool_calls: []``. The agent then has nothing to
execute, so the run loops uselessly until timeout.

This helper provides a parser-agnostic text-mode recovery: it pulls each
``<tool_call>...</tool_call>`` block out of the message content, attempts a
strict ``json.loads``, and on failure tries one round of brace-balance repair
(append ``}`` characters equal to ``count('{') - count('}')``). Successful
repairs are returned as standard tool-call dicts compatible with the existing
``_extract_tool_calls`` shape used by the Terminus-* agents.

The recovery is intentionally conservative:
- We only repair an opening-brace deficit (never strip extras).
- A single repair attempt; if it still fails to parse, the call is dropped.
- Each recovered call gets a synthetic id prefixed ``text-mode-tc-`` so it is
  traceable in trajectory dumps as a fallback-recovered call.
"""

from __future__ import annotations

import json
import re
from typing import Any
from uuid import uuid4

_TOOL_CALL_BLOCK_RE = re.compile(r"<tool_call>\s*(.*?)\s*</tool_call>", re.DOTALL)


def normalise_commands_arg(raw: Any) -> list[tuple[str, float]]:
    """Normalise an ``execute_commands.commands`` argument into a list of pairs.

    Open-weight models occasionally emit:

    - ``commands: "single string"``     (instead of a list)
    - ``commands: [str, str, ...]``      (list of bare strings)
    - ``commands: [{keystrokes, duration}, ...]``   (the schema-correct shape)
    - a list mixing both shapes

    Returns a normalised ``[(keystrokes, duration), ...]`` list. Unknown
    element shapes (ints, ``None``, etc.) are silently dropped. A non-list,
    non-string ``raw`` returns an empty list.
    """
    if isinstance(raw, str):
        raw = [raw]
    elif not isinstance(raw, list):
        return []
    out: list[tuple[str, float]] = []
    for cmd in raw:
        if isinstance(cmd, str):
            out.append((cmd, 1.0))
        elif isinstance(cmd, dict):
            ks = cmd.get("keystrokes", "")
            dur = cmd.get("duration", 1.0)
            try:
                dur = float(dur)
            except (TypeError, ValueError):
                dur = 1.0
            out.append((ks, dur))
        # else: silently drop unknown shape
    return out


def recover_text_mode_tool_calls(content: str) -> list[dict[str, Any]]:
    """Recover tool calls from a Hermes-format text payload with optional brace repair.

    Returns a list of tool-call dicts in the standard
    ``{id, type, function: {name, arguments}}`` shape used by the Terminus-*
    agents. Returns an empty list if no repairable blocks are found.
    """
    if not content:
        return []

    recovered: list[dict[str, Any]] = []
    for raw in _TOOL_CALL_BLOCK_RE.findall(content):
        parsed: dict[str, Any] | None = None
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            deficit = raw.count("{") - raw.count("}")
            if deficit > 0:
                try:
                    parsed = json.loads(raw + ("}" * deficit))
                except json.JSONDecodeError:
                    parsed = None

        if not isinstance(parsed, dict) or "name" not in parsed:
            continue

        arguments = parsed.get("arguments", {})
        if not isinstance(arguments, str):
            arguments = json.dumps(arguments)

        recovered.append(
            {
                "id": f"text-mode-tc-{uuid4().hex[:16]}",
                "type": "function",
                "function": {
                    "name": parsed["name"],
                    "arguments": arguments,
                },
            }
        )

    return recovered
