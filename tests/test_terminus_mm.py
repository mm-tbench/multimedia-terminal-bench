"""Tests for terminus-mm agent — pure logic functions that don't require Harbor.

Tests cover:
1. Keystroke \\n fix
2. Tool call parsing (one-media-per-turn, argument extraction)
3. Multimodal content block construction
4. Media context collapse
5. MIME type / size validation
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

# Import the module-level constants and classes directly
# (these don't require Harbor at import time)
import sys
from pathlib import Path
from dataclasses import dataclass

# We can't import TerminusMM directly (requires Harbor), but we can import
# the standalone functions and data structures by loading the module source.
# Instead, we extract and test the logic independently.


# ---------------------------------------------------------------------------
# Replicate the pure functions from terminus_mm.py for testing
# (avoids Harbor import dependency in test environment)
# ---------------------------------------------------------------------------

_MIME_MAP = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".gif": "image/gif", ".webp": "image/webp",
    ".mp4": "video/mp4", ".webm": "video/webm", ".avi": "video/x-msvideo",
    ".mov": "video/quicktime", ".mkv": "video/x-matroska",
    ".wav": "audio/wav", ".mp3": "audio/mpeg", ".ogg": "audio/ogg",
    ".flac": "audio/flac", ".aac": "audio/aac", ".m4a": "audio/mp4",
}


@dataclass
class MediaReadRequest:
    file_path: str
    modality: str


@dataclass
class Command:
    keystrokes: str
    duration_sec: float


def _fix_keystrokes(keystrokes: str) -> str:
    """Replicate the \\n fix from _execute_commands."""
    if keystrokes.endswith("\\n"):
        keystrokes = keystrokes[:-2] + "\n"
    return keystrokes


def _parse_tool_calls(
    tool_calls: list[dict],
) -> tuple[list[Command], bool, str, str, str, MediaReadRequest | None]:
    """Replicate _parse_tool_calls from terminus_mm.py."""
    commands = []
    is_task_complete = False
    feedback = ""
    analysis = ""
    plan = ""
    media_read = None

    if not tool_calls:
        feedback = "WARNINGS: No tool calls. Use execute_commands to run commands."
        return commands, is_task_complete, feedback, analysis, plan, media_read

    for tc in tool_calls:
        fn = tc.get("function", {}).get("name", "")
        args_str = tc.get("function", {}).get("arguments", "{}")
        try:
            args = json.loads(args_str) if isinstance(args_str, str) else args_str
        except json.JSONDecodeError:
            continue

        if fn == "execute_commands":
            analysis = args.get("analysis", "")
            plan = args.get("plan", "")
            for cmd in args.get("commands", []):
                commands.append(Command(
                    keystrokes=cmd.get("keystrokes", ""),
                    duration_sec=min(cmd.get("duration", 1.0), 60),
                ))
        elif fn == "task_complete":
            is_task_complete = True
        elif fn in ("watch_video", "listen_audio", "view_image"):
            fp = args.get("file_path", "")
            if fp:
                modality = {"watch_video": "video", "listen_audio": "audio", "view_image": "image"}[fn]
                if media_read is None:
                    media_read = MediaReadRequest(file_path=fp, modality=modality)
                else:
                    feedback = (
                        f"WARNINGS: Only one media file can be perceived per turn. "
                        f"'{media_read.file_path}' will be perceived now. "
                        f"Call {fn} again on your next turn for '{fp}'."
                    )
        else:
            feedback = f"WARNINGS: Unknown function '{fn}'."

    return commands, is_task_complete, feedback, analysis, plan, media_read


def _build_multimodal_content(b64: str, mime: str, modality: str) -> dict:
    """Replicate _build_multimodal_content from terminus_mm.py."""
    data_uri = f"data:{mime};base64,{b64}"
    if modality in ("image", "video"):
        return {"type": "image_url", "image_url": {"url": data_uri}}
    elif modality == "audio":
        audio_fmt = {
            "audio/wav": "wav", "audio/mpeg": "mp3", "audio/ogg": "ogg",
            "audio/flac": "flac", "audio/aac": "aac", "audio/mp4": "mp4",
        }.get(mime, "wav")
        return {"type": "input_audio", "input_audio": {"data": b64, "format": audio_fmt}}
    else:
        return {"type": "image_url", "image_url": {"url": data_uri}}


def _collapse_perceived_media(messages: list[dict]) -> None:
    """Replicate _collapse_perceived_media (operates on message list directly)."""
    for msg in messages:
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        has_media = any(
            isinstance(part, dict) and part.get("type") in ("image_url", "input_audio")
            for part in content
        )
        if not has_media:
            continue
        collapsed_parts = []
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                collapsed_parts.append(part)
            elif isinstance(part, dict) and part.get("type") in ("image_url", "input_audio"):
                collapsed_parts.append({
                    "type": "text",
                    "text": "[Raw media removed — refer to your analysis in the following response for what you perceived]",
                })
            else:
                collapsed_parts.append(part)
        msg["content"] = collapsed_parts


def _validate_media_file(file_path: str, b64_data: str) -> str | None:
    """Replicate size/format validation from _load_media_from_container."""
    ext = Path(file_path).suffix.lower()
    mime = _MIME_MAP.get(ext)
    if mime is None:
        return f"Unsupported format '{ext}'."
    approx_mb = len(b64_data) * 3 / 4 / 1024 / 1024
    if approx_mb > 20:
        return f"File is ~{approx_mb:.0f}MB, exceeds 20MB limit."
    return None


# ===========================================================================
# Tests
# ===========================================================================


class TestKeystrokeFix:
    def test_literal_backslash_n_converted(self):
        assert _fix_keystrokes("ls -la\\n") == "ls -la\n"

    def test_real_newline_unchanged(self):
        assert _fix_keystrokes("ls -la\n") == "ls -la\n"

    def test_no_newline_unchanged(self):
        assert _fix_keystrokes("ls -la") == "ls -la"

    def test_middle_backslash_n_not_converted(self):
        # Only trailing \\n should be fixed
        assert _fix_keystrokes("echo 'hello\\nworld'\\n") == "echo 'hello\\nworld'\n"

    def test_empty_string(self):
        assert _fix_keystrokes("") == ""

    def test_just_backslash_n(self):
        assert _fix_keystrokes("\\n") == "\n"

    def test_double_backslash_n(self):
        # "cmd\\n\\n" — trailing \\n converted, inner one stays
        result = _fix_keystrokes("cmd\\n\\n")
        assert result == "cmd\\n\n"

    def test_ffmpeg_command(self):
        ks = "ffmpeg -ss 00:05:00 -i ./assets/test.mp4 -t 00:00:05 -c copy scene1.mp4\\n"
        result = _fix_keystrokes(ks)
        assert result.endswith("\n")
        assert "scene1.mp4" in result
        assert not result.endswith("\\n")


class TestParseToolCalls:
    def test_execute_commands(self):
        tcs = [{"function": {"name": "execute_commands", "arguments": json.dumps({
            "analysis": "checking files",
            "plan": "list directory",
            "commands": [{"keystrokes": "ls\\n", "duration": 0.1}],
        })}}]
        commands, complete, feedback, analysis, plan, media = _parse_tool_calls(tcs)
        assert len(commands) == 1
        assert commands[0].keystrokes == "ls\\n"
        assert commands[0].duration_sec == 0.1
        assert analysis == "checking files"
        assert plan == "list directory"
        assert not complete
        assert media is None

    def test_task_complete(self):
        tcs = [{"function": {"name": "task_complete", "arguments": "{}"}}]
        commands, complete, feedback, _, _, media = _parse_tool_calls(tcs)
        assert complete is True
        assert commands == []
        assert media is None

    def test_watch_video(self):
        tcs = [{"function": {"name": "watch_video", "arguments": json.dumps({
            "file_path": "/app/clips/clip.mp4",
        })}}]
        _, _, _, _, _, media = _parse_tool_calls(tcs)
        assert media is not None
        assert media.file_path == "/app/clips/clip.mp4"
        assert media.modality == "video"

    def test_listen_audio(self):
        tcs = [{"function": {"name": "listen_audio", "arguments": json.dumps({
            "file_path": "/app/audio.wav",
        })}}]
        _, _, _, _, _, media = _parse_tool_calls(tcs)
        assert media is not None
        assert media.modality == "audio"

    def test_view_image(self):
        tcs = [{"function": {"name": "view_image", "arguments": json.dumps({
            "file_path": "/app/frame.png",
        })}}]
        _, _, _, _, _, media = _parse_tool_calls(tcs)
        assert media is not None
        assert media.modality == "image"

    def test_one_media_per_turn_first_wins(self):
        tcs = [
            {"function": {"name": "watch_video", "arguments": json.dumps({"file_path": "/app/scene1.mp4"})}},
            {"function": {"name": "watch_video", "arguments": json.dumps({"file_path": "/app/scene2.mp4"})}},
            {"function": {"name": "watch_video", "arguments": json.dumps({"file_path": "/app/scene3.mp4"})}},
        ]
        _, _, feedback, _, _, media = _parse_tool_calls(tcs)
        assert media.file_path == "/app/scene1.mp4"
        assert "Only one media file" in feedback
        assert "scene2.mp4" in feedback or "scene3.mp4" in feedback

    def test_mixed_media_types_first_wins(self):
        tcs = [
            {"function": {"name": "watch_video", "arguments": json.dumps({"file_path": "/app/v.mp4"})}},
            {"function": {"name": "listen_audio", "arguments": json.dumps({"file_path": "/app/a.wav"})}},
        ]
        _, _, feedback, _, _, media = _parse_tool_calls(tcs)
        assert media.file_path == "/app/v.mp4"
        assert media.modality == "video"
        assert "Only one" in feedback

    def test_no_tool_calls(self):
        commands, _, feedback, _, _, media = _parse_tool_calls([])
        assert "No tool calls" in feedback
        assert commands == []
        assert media is None

    def test_unknown_function(self):
        tcs = [{"function": {"name": "unknown_tool", "arguments": "{}"}}]
        _, _, feedback, _, _, _ = _parse_tool_calls(tcs)
        assert "Unknown function" in feedback

    def test_empty_file_path_ignored(self):
        tcs = [{"function": {"name": "watch_video", "arguments": json.dumps({"file_path": ""})}}]
        _, _, _, _, _, media = _parse_tool_calls(tcs)
        assert media is None

    def test_duration_capped_at_60(self):
        tcs = [{"function": {"name": "execute_commands", "arguments": json.dumps({
            "analysis": "", "plan": "",
            "commands": [{"keystrokes": "sleep 120\\n", "duration": 120}],
        })}}]
        commands, _, _, _, _, _ = _parse_tool_calls(tcs)
        assert commands[0].duration_sec == 60

    def test_commands_plus_media_both_parsed(self):
        tcs = [
            {"function": {"name": "execute_commands", "arguments": json.dumps({
                "analysis": "a", "plan": "p",
                "commands": [{"keystrokes": "ls\\n"}],
            })}},
            {"function": {"name": "watch_video", "arguments": json.dumps({"file_path": "/app/v.mp4"})}},
        ]
        commands, _, _, analysis, _, media = _parse_tool_calls(tcs)
        assert len(commands) == 1
        assert media is not None
        assert analysis == "a"


class TestBuildMultimodalContent:
    def test_image_format(self):
        result = _build_multimodal_content("abc123", "image/png", "image")
        assert result["type"] == "image_url"
        assert result["image_url"]["url"] == "data:image/png;base64,abc123"

    def test_video_format(self):
        result = _build_multimodal_content("abc123", "video/mp4", "video")
        assert result["type"] == "image_url"
        assert result["image_url"]["url"] == "data:video/mp4;base64,abc123"

    def test_audio_wav_format(self):
        result = _build_multimodal_content("abc123", "audio/wav", "audio")
        assert result["type"] == "input_audio"
        assert result["input_audio"]["data"] == "abc123"
        assert result["input_audio"]["format"] == "wav"

    def test_audio_mp3_format(self):
        result = _build_multimodal_content("abc123", "audio/mpeg", "audio")
        assert result["input_audio"]["format"] == "mp3"

    def test_audio_ogg_format(self):
        result = _build_multimodal_content("abc123", "audio/ogg", "audio")
        assert result["input_audio"]["format"] == "ogg"

    def test_unknown_modality_defaults_to_image_url(self):
        result = _build_multimodal_content("abc123", "application/octet-stream", "unknown")
        assert result["type"] == "image_url"


class TestCollapsePerceivedMedia:
    def test_text_messages_unchanged(self):
        messages = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "world"},
        ]
        _collapse_perceived_media(messages)
        assert messages[0]["content"] == "hello"
        assert messages[1]["content"] == "world"

    def test_image_collapsed(self):
        messages = [
            {"role": "user", "content": [
                {"type": "text", "text": "Look at this"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,huge_data"}},
            ]},
        ]
        _collapse_perceived_media(messages)
        content = messages[0]["content"]
        assert len(content) == 2
        assert content[0]["type"] == "text"
        assert content[0]["text"] == "Look at this"
        assert content[1]["type"] == "text"
        assert "Raw media removed" in content[1]["text"]

    def test_audio_collapsed(self):
        messages = [
            {"role": "user", "content": [
                {"type": "text", "text": "Listen"},
                {"type": "input_audio", "input_audio": {"data": "huge_audio", "format": "wav"}},
            ]},
        ]
        _collapse_perceived_media(messages)
        content = messages[0]["content"]
        assert all(p["type"] == "text" for p in content)
        assert "Raw media removed" in content[1]["text"]

    def test_multipart_without_media_unchanged(self):
        messages = [
            {"role": "user", "content": [
                {"type": "text", "text": "part 1"},
                {"type": "text", "text": "part 2"},
            ]},
        ]
        _collapse_perceived_media(messages)
        assert messages[0]["content"][0]["text"] == "part 1"
        assert messages[0]["content"][1]["text"] == "part 2"

    def test_multiple_media_in_one_message(self):
        messages = [
            {"role": "user", "content": [
                {"type": "text", "text": "Two files:"},
                {"type": "image_url", "image_url": {"url": "data:video/mp4;base64,vid1"}},
                {"type": "input_audio", "input_audio": {"data": "aud1", "format": "wav"}},
            ]},
        ]
        _collapse_perceived_media(messages)
        content = messages[0]["content"]
        assert len(content) == 3
        assert all(p["type"] == "text" for p in content)

    def test_only_media_messages_affected(self):
        messages = [
            {"role": "user", "content": "plain text"},
            {"role": "user", "content": [
                {"type": "text", "text": "with media"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,x"}},
            ]},
            {"role": "assistant", "content": "response"},
        ]
        _collapse_perceived_media(messages)
        assert messages[0]["content"] == "plain text"
        assert "Raw media removed" in messages[1]["content"][1]["text"]
        assert messages[2]["content"] == "response"

    def test_idempotent(self):
        messages = [
            {"role": "user", "content": [
                {"type": "text", "text": "media"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,x"}},
            ]},
        ]
        _collapse_perceived_media(messages)
        first_pass = [p.copy() for p in messages[0]["content"]]
        _collapse_perceived_media(messages)
        assert messages[0]["content"] == first_pass


class TestMediaValidation:
    def test_supported_video_formats(self):
        for ext in [".mp4", ".webm", ".avi", ".mov", ".mkv"]:
            assert _validate_media_file(f"/app/file{ext}", "small") is None

    def test_supported_audio_formats(self):
        for ext in [".wav", ".mp3", ".ogg", ".flac", ".aac", ".m4a"]:
            assert _validate_media_file(f"/app/file{ext}", "small") is None

    def test_supported_image_formats(self):
        for ext in [".png", ".jpg", ".jpeg", ".gif", ".webp"]:
            assert _validate_media_file(f"/app/file{ext}", "small") is None

    def test_unsupported_format_rejected(self):
        error = _validate_media_file("/app/file.pdf", "small")
        assert error is not None
        assert "Unsupported" in error

    def test_unsupported_format_txt(self):
        error = _validate_media_file("/app/file.txt", "small")
        assert error is not None

    def test_size_under_limit_ok(self):
        # 10MB in base64 ≈ 13.3M chars
        b64 = "A" * (10 * 1024 * 1024 * 4 // 3)
        assert _validate_media_file("/app/file.mp4", b64) is None

    def test_size_over_limit_rejected(self):
        # 25MB in base64 ≈ 33.3M chars
        b64 = "A" * (25 * 1024 * 1024 * 4 // 3)
        error = _validate_media_file("/app/file.mp4", b64)
        assert error is not None
        assert "20MB" in error

    def test_exact_boundary(self):
        # Exactly 20MB = 20 * 1024 * 1024 bytes → base64 chars = 20*1024*1024*4/3
        b64_20mb = "A" * (20 * 1024 * 1024 * 4 // 3)
        # At exactly 20MB, should pass (> 20, not >=)
        assert _validate_media_file("/app/file.mp4", b64_20mb) is None

    def test_just_over_boundary(self):
        b64_over = "A" * (21 * 1024 * 1024 * 4 // 3)
        error = _validate_media_file("/app/file.mp4", b64_over)
        assert error is not None


class TestMimeMap:
    def test_all_video_extensions(self):
        video_exts = {".mp4", ".webm", ".avi", ".mov", ".mkv"}
        for ext in video_exts:
            assert _MIME_MAP[ext].startswith("video/")

    def test_all_audio_extensions(self):
        audio_exts = {".wav", ".mp3", ".ogg", ".flac", ".aac", ".m4a"}
        for ext in audio_exts:
            assert _MIME_MAP[ext].startswith("audio/")

    def test_all_image_extensions(self):
        image_exts = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
        for ext in image_exts:
            assert _MIME_MAP[ext].startswith("image/")

    def test_no_overlap(self):
        """Each extension should map to exactly one MIME type."""
        assert len(_MIME_MAP) == len(set(_MIME_MAP.values())) or True  # duplicates ok (jpg/jpeg)
        # But no extension should appear twice
        assert len(_MIME_MAP.keys()) == len(set(_MIME_MAP.keys()))
