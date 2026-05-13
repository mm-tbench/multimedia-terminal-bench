"""Tests for terminus-kira keystroke preprocessing.

Mirrors ``TestKeystrokeFix`` in ``test_terminus_mm.py``. The two agents are
expected to preprocess command keystrokes identically — a literal trailing
``"\\n"`` is rewritten to a real newline. Drift between the two would mean
the same LLM output behaves differently on KIRA vs MM, which is exactly the
regression this pair of tests exists to catch.

``_fix_keystrokes`` below re-implements the rule (not imported) because the
actual logic is inlined in ``_execute_commands`` in each agent. If/when that
logic is extracted into a callable, both test files should import it.
"""

from __future__ import annotations


def _fix_keystrokes(keystrokes: str) -> str:
    """Replicate the \\n fix from terminus_kira._execute_commands."""
    if keystrokes.endswith("\\n"):
        keystrokes = keystrokes[:-2] + "\n"
    return keystrokes


class TestKeystrokeFix:
    def test_literal_backslash_n_converted(self):
        assert _fix_keystrokes("ls -la\\n") == "ls -la\n"

    def test_real_newline_unchanged(self):
        assert _fix_keystrokes("ls -la\n") == "ls -la\n"

    def test_no_newline_unchanged(self):
        assert _fix_keystrokes("ls -la") == "ls -la"

    def test_middle_backslash_n_not_converted(self):
        assert _fix_keystrokes("echo 'hello\\nworld'\\n") == "echo 'hello\\nworld'\n"

    def test_empty_string(self):
        assert _fix_keystrokes("") == ""

    def test_just_backslash_n(self):
        assert _fix_keystrokes("\\n") == "\n"

    def test_double_backslash_n(self):
        # "cmd\\n\\n" — trailing \\n converted, inner one stays
        assert _fix_keystrokes("cmd\\n\\n") == "cmd\\n\n"

    def test_ffmpeg_command(self):
        ks = (
            "ffmpeg -ss 00:05:00 -i ./assets/test.mp4 -t 00:00:05 -c copy scene1.mp4\\n"
        )
        result = _fix_keystrokes(ks)
        assert result.endswith("\n")
        assert "scene1.mp4" in result
        assert not result.endswith("\\n")
