"""
Terminus-MM — Terminus-2/KIRA with multimedia perception.

Lineage: Terminus-2 (Harbor) → Terminus-KIRA (KRAFTON AI) → Terminus-MM (MMTB)

Terminus-2: terminal agent with tmux-based container interaction
Terminus-KIRA: adds native tool calling, image_read, marker-based polling
Terminus-MM: adds watch_video, listen_audio — media injected into the agent's
             own conversation context so the model perceives content directly.

Key difference from KIRA's image_read: KIRA makes a separate LLM call and returns
a text summary. Terminus-MM injects the raw media into the agent's chat history.
The model sees/hears the content itself and reasons about it in subsequent turns.

Usage:
    harbor run -p datasets/mmtb-core/<task> \\
        --agent-import-path "mmtb_runtime.agent:TerminusMM" \\
        -m gemini/gemini-3.1-pro-preview
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import litellm

# Suppress "Provider List: ..." warnings printed when a model isn't in LiteLLM's
# pricing registry (e.g. openrouter/google/gemini-2.5-pro). The API calls still
# succeed; only the model-info lookup fails, which spams stdout via raw print().
litellm.suppress_debug_info = True

from litellm.exceptions import (
    AuthenticationError as LiteLLMAuthenticationError,
    BadRequestError,
    ContextWindowExceededError as LiteLLMContextWindowExceededError,
)
from tenacity import (
    retry,
    retry_if_exception_type,
    retry_if_not_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from harbor.agents.terminus_2 import Terminus2
from harbor.agents.terminus_2.terminus_2 import Command
from harbor.agents.terminus_2.tmux_session import TmuxSession
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext
from mmtb_runtime.agent._tool_call_recovery import (
    normalise_commands_arg,
    recover_text_mode_tool_calls,
)
from harbor.llms.base import (
    ContextLengthExceededError,
    LLMResponse,
    OutputLengthExceededError,
)
from harbor.llms.chat import Chat
from harbor.models.metric import UsageInfo
from harbor.models.trajectories import (
    Metrics,
    Observation,
    ObservationResult,
    Step,
    ToolCall,
)

from mmtb_runtime.agent._max_tokens import resolve_max_tokens
from mmtb_runtime.agent.anthropic_caching import add_anthropic_caching


class BlockError(Exception):
    pass


BLOCK_TIMEOUT_SEC = 600
_MARKER_PREFIX = "__CMDEND__"


@dataclass
class ToolCallResponse:
    content: str | None
    tool_calls: list[dict[str, Any]]
    reasoning_content: str | None = None
    usage: UsageInfo | None = None


@dataclass
class MediaReadRequest:
    """Request to perceive a media file."""

    file_path: str
    modality: str  # "image", "video", "audio"


# ---------------------------------------------------------------------------
# Tool descriptions
# ---------------------------------------------------------------------------

_EXECUTE_COMMANDS_DESC = (
    "Call this to execute commands in the terminal with your analysis and plan."
)

_ANALYSIS_DESC = (
    "Analyze the current state based on the terminal output provided. "
    "What do you see? What has been accomplished? What still needs to be done?"
)
_PLAN_DESC = (
    "Describe your plan for the next steps. What commands will you run and why?"
)
_COMMANDS_DESC = (
    "The commands array can be empty if you want to wait without taking action."
)
_KEYSTROKES_DESC = (
    "String containing the exact keystrokes to send to the terminal. "
    "Most bash commands should end with a newline (\\n). "
    "For special keys: C-c for Ctrl+C, C-d for Ctrl+D."
)
_DURATION_DESC = (
    "Seconds to wait for the command to complete (default: 1.0). "
    "Immediate tasks: 0.1s. Slow commands: set appropriately. Max: 60s."
)
_TASK_COMPLETE_DESC = "Call this when the task is complete."

_WATCH_VIDEO_DESC = (
    "Watch a video file. The video content will be injected into your context — "
    "you will see the visuals and hear the audio directly in your next turn. "
    "Only ONE file can be perceived per turn. The raw media is available for "
    "ONE turn only, then replaced by your written analysis. "
    "For large files (>20MB), extract a shorter segment first with ffmpeg."
)
_LISTEN_AUDIO_DESC = (
    "Listen to an audio file. The audio content will be injected into your context — "
    "you will hear it directly in your next turn. "
    "Only ONE file can be perceived per turn. The raw audio is available for "
    "ONE turn only, then replaced by your written analysis."
)
_VIEW_IMAGE_DESC = (
    "View an image file. The image will be injected into your context — "
    "you will see it directly in your next turn. "
    "Only ONE file can be perceived per turn. The raw image is available for "
    "ONE turn only, then replaced by your written analysis. "
    "Supported formats: PNG, JPG, JPEG, GIF, WEBP."
)
_FILE_PATH_DESC = "Absolute path to the file in the container."

# ---------------------------------------------------------------------------
# Tool definitions (native LLM tool calling)
# ---------------------------------------------------------------------------

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "execute_commands",
            "description": _EXECUTE_COMMANDS_DESC,
            "parameters": {
                "type": "object",
                "properties": {
                    "analysis": {"type": "string", "description": _ANALYSIS_DESC},
                    "plan": {"type": "string", "description": _PLAN_DESC},
                    "commands": {
                        "type": "array",
                        "description": _COMMANDS_DESC,
                        "items": {
                            "type": "object",
                            "properties": {
                                "keystrokes": {
                                    "type": "string",
                                    "description": _KEYSTROKES_DESC,
                                },
                                "duration": {
                                    "type": "number",
                                    "description": _DURATION_DESC,
                                },
                            },
                            "required": ["keystrokes"],
                        },
                    },
                },
                "required": ["analysis", "plan", "commands"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "task_complete",
            "description": _TASK_COMPLETE_DESC,
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "watch_video",
            "description": _WATCH_VIDEO_DESC,
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {"type": "string", "description": _FILE_PATH_DESC},
                },
                "required": ["file_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "listen_audio",
            "description": _LISTEN_AUDIO_DESC,
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {"type": "string", "description": _FILE_PATH_DESC},
                },
                "required": ["file_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "view_image",
            "description": _VIEW_IMAGE_DESC,
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {"type": "string", "description": _FILE_PATH_DESC},
                },
                "required": ["file_path"],
            },
        },
    },
]

# Supported MIME types
_MIME_MAP = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".mp4": "video/mp4",
    ".webm": "video/webm",
    ".avi": "video/x-msvideo",
    ".mov": "video/quicktime",
    ".mkv": "video/x-matroska",
    ".wav": "audio/wav",
    ".mp3": "audio/mpeg",
    ".ogg": "audio/ogg",
    ".flac": "audio/flac",
    ".aac": "audio/aac",
    ".m4a": "audio/mp4",
}


# ---------------------------------------------------------------------------
# Workspace-scan routing (perception tool + prompt-template selection)
# ---------------------------------------------------------------------------
WORKSPACE_SCAN_ROOT = "/app"
WORKSPACE_SCAN_MAXDEPTH = 6

_PERCEPTION_TOOL_NAMES = {"watch_video", "listen_audio", "view_image"}
_ALWAYS_KEEP = {"execute_commands", "task_complete"}

_EXT_TO_TOOL = {
    # audio
    ".wav": "listen_audio",
    ".mp3": "listen_audio",
    ".ogg": "listen_audio",
    ".flac": "listen_audio",
    ".aac": "listen_audio",
    ".m4a": "listen_audio",
    # video
    ".mp4": "watch_video",
    ".webm": "watch_video",
    ".avi": "watch_video",
    ".mov": "watch_video",
    ".mkv": "watch_video",
    # image
    ".png": "view_image",
    ".jpg": "view_image",
    ".jpeg": "view_image",
    ".gif": "view_image",
    ".webp": "view_image",
}

# Prompt-template key → file mapping (resolved relative to this module's dir).
_PROMPT_DIR = Path(__file__).parent
_PROMPT_FILES = {
    "mm": _PROMPT_DIR / "prompt-template.txt",  # canonical (audio + video)
    "ia": _PROMPT_DIR / "prompt-template-ia.txt",  # image + audio
    "iv": _PROMPT_DIR / "prompt-template-iv.txt",  # image + video
    "a": _PROMPT_DIR / "prompt-template-a.txt",  # audio only
}


class TerminusMM(Terminus2):
    """
    Terminus-MM: Terminus-2/KIRA extended with multimedia perception.

    Adds watch_video, listen_audio, view_image tools that inject raw media
    into the agent's conversation context. The model perceives the content
    directly — no separate summarization call.

    At workspace scan time, TerminusMM selects two things based on which
    media files are present in the container:

    1. The LLM tool schema — only perception tools whose modality matches
       at least one file in the workspace are exposed.
    2. The system prompt template — picked so the prompt advertises exactly
       the schema that was routed (keeps prompt and schema consistent).

    Routing → prompt-template mapping
    ---------------------------------
      audio file present, no video      → prompt-template-ia.txt   (image+audio ablation)
      video file present, no audio      → prompt-template-iv.txt   (image+video ablation)
      audio + video both present        → prompt-template.txt      (MM canonical)
      audio only, no image              → prompt-template-a.txt    (audio-only)
      no media at all                   → MM canonical (text-only fallback)

    The "ablation" prompts (IA/IV/A) are MM's canonical prompt minus the
    missing-tool blocks plus a single-sentence "you cannot call X" notice —
    pure ablations.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._marker_seq = 0
        self._total_time_saved = 0.0
        self._media_just_perceived = (
            False  # True after media injection, forces analysis next turn
        )
        # Workspace-routing cache populated lazily on first LLM call.
        self._routed_tools: list[dict] | None = None
        self._routed_tool_names: list[str] | None = None
        self._routed_prompt_key: str | None = None
        self._routed_prompt_path: Path | None = None

    async def _with_block_timeout(self, coro, timeout_sec: int = BLOCK_TIMEOUT_SEC):
        try:
            return await asyncio.wait_for(coro, timeout=timeout_sec)
        except asyncio.TimeoutError:
            raise BlockError(f"Infrastructure API blocked for {timeout_sec}s")

    # ===== Command execution with marker-based polling (from KIRA) =====

    async def _execute_commands(
        self,
        commands: list[Command],
        session: TmuxSession,
    ) -> tuple[bool, str]:
        for command in commands:
            self._marker_seq += 1
            marker = f"{_MARKER_PREFIX}{self._marker_seq}__"
            start = time.monotonic()

            keystrokes = command.keystrokes

            # Fix: LLMs often output literal "\n" (backslash + n) instead of an
            # actual newline character. If sent to tmux as-is, the shell interprets
            # the backslash-n as a regular character, and the subsequent marker echo
            # concatenates with the command (e.g., "file.mp4\necho" → "file.mp4necho").
            # Convert trailing literal "\n" to a real newline.
            if keystrokes.endswith("\\n"):
                keystrokes = keystrokes[:-2] + "\n"

            await session.send_keys(keystrokes, block=False, min_timeout_sec=0.0)
            # Wait for the shell to process the newline before sending the marker
            await asyncio.sleep(0.05)
            await session.send_keys(
                f"echo '{marker}'\n", block=False, min_timeout_sec=0.0
            )

            await asyncio.sleep(min(0.3, command.duration_sec))
            while time.monotonic() - start < command.duration_sec:
                pane_content = await session.capture_pane()
                if marker in pane_content:
                    break
                await asyncio.sleep(0.5)

            saved = command.duration_sec - (time.monotonic() - start)
            if saved > 0.1:
                self._total_time_saved += saved

        output = await session.get_incremental_output()
        markers = {f"{_MARKER_PREFIX}{seq}__" for seq in range(1, self._marker_seq + 1)}
        lines = [
            line for line in output.split("\n") if not any(m in line for m in markers)
        ]
        return False, self._limit_output_length("\n".join(lines))

    # ===== Agent metadata =====

    @staticmethod
    def name() -> str:
        return "terminus-mm"

    def version(self) -> str | None:
        return "0.1.0"

    async def run(
        self, instruction: str, environment: BaseEnvironment, context: AgentContext
    ) -> None:
        self._original_instruction = instruction
        await super().run(instruction, environment, context)

    def _count_total_tokens(self, chat: "Chat") -> int:  # noqa: F821
        """Count tokens, substituting unsupported audio blocks with a placeholder.

        litellm's token_counter (as of 1.79+) rejects OpenAI-format
        ``input_audio`` content blocks with
        ``ValueError: Invalid content item type: input_audio``. That content
        type is only present briefly in the chat — just after the agent reads
        an audio file and before ``_collapse_perceived_media`` replaces it with
        a text placeholder. A single proactive-summarization check during that
        window crashes the entire run.

        We substitute each ``input_audio`` block with a text placeholder whose
        length approximates Gemini's 32 tokens/sec audio tokenization rate
        (base64 → bytes → seconds → tokens). That keeps the summarization
        heuristic roughly accurate without calling into a code path that
        doesn't know about audio yet.
        """
        import base64 as _b64
        import copy

        from litellm.utils import token_counter  # noqa: PLC0415

        messages = copy.deepcopy(chat.messages)
        for msg in messages:
            content = msg.get("content")
            if not isinstance(content, list):
                continue
            for i, part in enumerate(content):
                if not (isinstance(part, dict) and part.get("type") == "input_audio"):
                    continue
                data = part.get("input_audio", {}).get("data", "")
                # Estimate tokens: assume ~16 kbit/s audio, and Gemini's 32
                # tokens/sec rate. base64 inflates bytes by 4/3.
                try:
                    byte_len = len(_b64.b64decode(data, validate=False))
                except Exception:
                    byte_len = int(len(data) * 3 / 4)
                est_seconds = max(1, byte_len // 2000)  # ~16 kbit/s
                est_tokens = est_seconds * 32
                # Substitute a string that roughly tokenizes to est_tokens
                # (one whitespace-separated short word ≈ 1 token).
                placeholder = "audio " * est_tokens
                content[i] = {"type": "text", "text": placeholder.strip()}
        return token_counter(model=self._model_name, messages=messages)

    def _get_parser(self):
        return None

    def _get_prompt_template_path(self) -> Path:
        # Initial path; will be overridden after the workspace scan via
        # _scan_and_route() if a non-canonical prompt is selected. The parent
        # class reads the template once at construction; we reload it lazily
        # by patching `self._prompt_template` after the first scan completes.
        return _PROMPT_FILES["mm"]

    def _get_error_response_type(self) -> str:
        return "response with valid tool calls"

    def _get_completion_confirmation_message(self, terminal_output: str) -> str:
        instruction = getattr(self, "_original_instruction", "N/A")
        return (
            f"Original task:\n{instruction}\n\n"
            f"Current terminal state:\n{terminal_output}\n\n"
            "Are you sure you want to mark the task as complete?\n"
            "Verify your solution meets all requirements, then call task_complete again."
        )

    def _limit_output_length(self, output: str, max_bytes: int = 30000) -> str:
        return super()._limit_output_length(output, max_bytes)

    # ===== LLM helpers =====

    def _extract_tool_calls(self, response) -> list[dict[str, Any]]:
        tool_calls = []
        try:
            message = response.choices[0].message
            if hasattr(message, "tool_calls") and message.tool_calls:
                for tc in message.tool_calls:
                    tool_calls.append(
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.function.name,
                                "arguments": tc.function.arguments,
                            },
                        }
                    )
        except (AttributeError, IndexError):
            pass
        if not tool_calls:
            try:
                content = response.choices[0].message.content or ""
            except (AttributeError, IndexError):
                content = ""
            tool_calls.extend(recover_text_mode_tool_calls(content))
        return tool_calls

    def _extract_usage_info(self, response) -> UsageInfo | None:
        try:
            usage = response.usage
            if usage:
                cost = 0.0
                try:
                    cost = litellm.completion_cost(completion_response=response) or 0.0
                except Exception:
                    pass
                return UsageInfo(
                    prompt_tokens=usage.prompt_tokens or 0,
                    completion_tokens=usage.completion_tokens or 0,
                    cache_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
                    cost_usd=cost,
                )
        except (AttributeError, TypeError):
            pass
        return None

    def _parse_tool_calls(
        self, tool_calls: list[dict[str, Any]]
    ) -> tuple[list[Command], bool, str, str, str, MediaReadRequest | None]:
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
                for keystrokes, duration in normalise_commands_arg(
                    args.get("commands", [])
                ):
                    commands.append(
                        Command(
                            keystrokes=keystrokes,
                            duration_sec=min(duration, 60),
                        )
                    )
            elif fn == "task_complete":
                is_task_complete = True
            elif fn in ("watch_video", "listen_audio", "view_image"):
                fp = args.get("file_path", "")
                if fp:
                    modality = {
                        "watch_video": "video",
                        "listen_audio": "audio",
                        "view_image": "image",
                    }[fn]
                    if media_read is None:
                        media_read = MediaReadRequest(file_path=fp, modality=modality)
                    else:
                        # Only one media perception per turn — queue the rest
                        feedback = (
                            f"WARNINGS: Only one media file can be perceived per turn. "
                            f"'{media_read.file_path}' will be perceived now. "
                            f"Call {fn} again on your next turn for '{fp}'."
                        )
            else:
                feedback = f"WARNINGS: Unknown function '{fn}'."

        return commands, is_task_complete, feedback, analysis, plan, media_read

    # ===== Media perception — inject into conversation context =====

    async def _load_media_from_container(
        self,
        file_path: str,
    ) -> tuple[str | None, str | None, str | None]:
        """Read a media file from the container and return (b64, mime, error).

        Returns (base64_data, mime_type, None) on success,
        or (None, None, error_message) on failure.
        """
        if self._session is None:
            return None, None, "Session is not set"

        ext = Path(file_path).suffix.lower()
        mime = _MIME_MAP.get(ext)
        if mime is None:
            return (
                None,
                None,
                (
                    f"Unsupported format '{ext}'. "
                    f"Supported: {', '.join(sorted(_MIME_MAP.keys()))}"
                ),
            )

        result = await self._with_block_timeout(
            self._session.environment.exec(command=f"base64 -w0 {file_path}")
        )
        if result.return_code != 0:
            return (
                None,
                None,
                f"Failed to read '{file_path}': {result.stderr or 'unknown error'}",
            )

        b64 = (result.stdout or "").strip()
        if not b64:
            return None, None, f"File '{file_path}' is empty or could not be read."

        approx_mb = len(b64) * 3 / 4 / 1024 / 1024
        if approx_mb > 20:
            return (
                None,
                None,
                (
                    f"File '{file_path}' is ~{approx_mb:.0f}MB, exceeds 20MB limit. "
                    f"Use ffmpeg to extract a shorter segment first."
                ),
            )

        return b64, mime, None

    def _build_multimodal_content(self, b64: str, mime: str, modality: str) -> dict:
        """Build an OpenAI-format multimodal content part from base64 data."""
        data_uri = f"data:{mime};base64,{b64}"

        if modality in ("image", "video"):
            # LiteLLM translates image_url data URIs to provider-native format
            # (Gemini inline_data, etc.) — works for both images and video
            return {"type": "image_url", "image_url": {"url": data_uri}}
        elif modality == "audio":
            audio_fmt = {
                "audio/wav": "wav",
                "audio/mpeg": "mp3",
                "audio/ogg": "ogg",
                "audio/flac": "flac",
                "audio/aac": "aac",
                "audio/mp4": "mp4",
            }.get(mime, "wav")
            return {
                "type": "input_audio",
                "input_audio": {"data": b64, "format": audio_fmt},
            }
        else:
            return {"type": "image_url", "image_url": {"url": data_uri}}

    def _collapse_perceived_media(self, chat: Chat) -> None:
        """Replace multimodal content in chat history with text placeholders.

        Called AFTER the model has perceived media (i.e., after the LLM call
        that included the multimodal content). This prevents re-sending large
        base64 data on every subsequent turn.

        The model's own response from the perception turn serves as its
        memory of what it saw/heard.
        """
        for msg in chat._messages:
            content = msg.get("content")
            if not isinstance(content, list):
                continue

            has_media = any(
                isinstance(part, dict)
                and part.get("type") in ("image_url", "input_audio")
                for part in content
            )
            if not has_media:
                continue

            # Collapse: keep text parts, replace media with a note.
            # The model's own analysis from the next assistant message
            # serves as the persistent record of what it perceived.
            collapsed_parts = []
            for part in content:
                if isinstance(part, dict) and part.get("type") == "text":
                    collapsed_parts.append(part)
                elif isinstance(part, dict) and part.get("type") in (
                    "image_url",
                    "input_audio",
                ):
                    collapsed_parts.append(
                        {
                            "type": "text",
                            "text": "[Raw media removed — refer to your analysis in the following response for what you perceived]",
                        }
                    )
                else:
                    collapsed_parts.append(part)

            msg["content"] = collapsed_parts

    async def _inject_media_into_context(
        self,
        media_read: MediaReadRequest,
        chat: Chat,
    ) -> str:
        """Load media and inject it into the agent's conversation context.

        Instead of making a separate LLM call (like KIRA's image_read),
        this adds the raw media as a multimodal content block to chat._messages.
        The model perceives the content directly in its next turn.

        After the next LLM call, _collapse_perceived_media() replaces the raw
        media with a text placeholder to prevent context overflow.

        Returns a text observation for the agent describing what was loaded.
        """
        # Collapse any previously-injected media before adding new one
        self._collapse_perceived_media(chat)

        b64, mime, error = await self._load_media_from_container(media_read.file_path)
        if error:
            return f"ERROR: {error}"

        media_content = self._build_multimodal_content(b64, mime, media_read.modality)
        basename = Path(media_read.file_path).name
        modality_label = {"video": "Video", "audio": "Audio", "image": "Image"}.get(
            media_read.modality, "Media"
        )

        # Inject the media into chat history as a user message with multimodal content
        chat._messages.append(
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": f"[{modality_label} loaded: {basename}] Perceive this {media_read.modality} and use it to complete the task.",
                    },
                    media_content,
                ],
            }
        )
        chat.reset_response_chain()

        approx_mb = len(b64) * 3 / 4 / 1024 / 1024
        return (
            f"{modality_label} '{basename}' ({approx_mb:.1f}MB) has been loaded into your context. "
            f"You can now perceive its content directly.\n\n"
            f"IMPORTANT: This raw media will be removed after this turn. "
            f"You MUST call execute_commands NOW with a thorough analysis of everything you perceive. "
            f"Be as detailed and specific as possible — this written analysis will be your only "
            f"record of this media in subsequent turns.\n\n"
            f"Media perception tools are temporarily unavailable until you provide your analysis."
        )

    # ===== LLM call with tools =====

    @retry(
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=0.5, min=0.5, max=4),
        retry=(
            retry_if_exception_type(Exception)
            & retry_if_not_exception_type(
                (
                    BadRequestError,
                    LiteLLMAuthenticationError,
                    ContextLengthExceededError,
                    OutputLengthExceededError,
                )
            )
        ),
        reraise=True,
    )
    async def _scan_and_route(self) -> list[dict]:
        """Scan workspace, decide tools + prompt, cache both. Idempotent.

        Selects the LLM tool schema and the system prompt template based on
        which media-file extensions actually exist in the workspace, so the
        prompt advertises exactly the modalities the schema exposes.
        """
        if self._routed_tools is not None:
            return self._routed_tools

        keep = set(_ALWAYS_KEEP)
        modality = {"audio": False, "video": False, "image": False}

        if self._session is not None:
            exts = sorted(_EXT_TO_TOOL.keys())
            name_clauses = " -o ".join(f"-iname '*{ext}'" for ext in exts)
            cmd = (
                f"find {WORKSPACE_SCAN_ROOT} -maxdepth {WORKSPACE_SCAN_MAXDEPTH} "
                f"-type f \\( {name_clauses} \\) 2>/dev/null"
            )
            try:
                result = await self._with_block_timeout(
                    self._session.environment.exec(command=cmd)
                )
                out = (result.stdout or "") if result.return_code == 0 else ""
            except Exception:
                out = ""
            for line in out.splitlines():
                line = line.strip()
                if not line or "." not in line:
                    continue
                ext = "." + line.rsplit(".", 1)[-1].lower()
                tool = _EXT_TO_TOOL.get(ext)
                if tool == "listen_audio":
                    modality["audio"] = True
                elif tool == "watch_video":
                    modality["video"] = True
                elif tool == "view_image":
                    modality["image"] = True

            any_media = any(modality.values())
            if any_media:
                keep.add("view_image")
                if modality["audio"]:
                    keep.add("listen_audio")
                if modality["video"]:
                    keep.add("watch_video")

        # Tool-list routing decision.
        routed = [t for t in TOOLS if t["function"]["name"] in keep]
        self._routed_tools = routed
        self._routed_tool_names = sorted(t["function"]["name"] for t in routed)

        # Prompt-template routing decision.
        has_video = "watch_video" in keep
        has_audio = "listen_audio" in keep
        has_image = "view_image" in keep
        if has_video and has_audio:
            prompt_key = "mm"
        elif has_video and has_image and not has_audio:
            prompt_key = "iv"
        elif has_audio and has_image and not has_video:
            prompt_key = "ia"
        elif has_audio and not has_video and not has_image:
            prompt_key = "a"
        else:
            # No media or unusual subset: fall back to MM canonical.
            prompt_key = "mm"
        self._routed_prompt_key = prompt_key
        self._routed_prompt_path = _PROMPT_FILES[prompt_key]
        # Hot-swap the cached system prompt.
        try:
            self._prompt_template = self._routed_prompt_path.read_text()
        except Exception:
            # If the file cannot be read for any reason, keep whatever the
            # parent class already loaded — better degrade silently than
            # crash the run.
            pass

        print(
            f"[terminus-mm] routed tools = {self._routed_tool_names} "
            f"prompt = {prompt_key}"
        )
        return routed

    async def _call_llm_with_tools(self, messages: list[dict]) -> ToolCallResponse:
        messages = add_anthropic_caching(messages, self._model_name)

        routed_tools = await self._scan_and_route()

        # After media perception, temporarily remove media tools to force the
        # model to analyze what it perceived via execute_commands (which has
        # the analysis/plan fields). This prevents the model from endlessly
        # calling watch_video without ever writing its observations.
        if self._media_just_perceived:
            tools = [
                t
                for t in routed_tools
                if t["function"]["name"] not in _PERCEPTION_TOOL_NAMES
            ]
        else:
            tools = routed_tools

        completion_kwargs = {
            "model": self._model_name,
            "messages": messages,
            "temperature": self._temperature,
            "tools": tools,
            "timeout": 900,
            "max_tokens": resolve_max_tokens(self._model_name),
            "drop_params": True,
        }
        if hasattr(self._llm, "_api_base") and self._llm._api_base:
            completion_kwargs["api_base"] = self._llm._api_base
        if self._reasoning_effort:
            completion_kwargs["reasoning_effort"] = self._reasoning_effort
            completion_kwargs["temperature"] = 1

        try:
            response = await litellm.acompletion(**completion_kwargs)
        except LiteLLMContextWindowExceededError:
            raise ContextLengthExceededError()

        message = response.choices[0].message
        content = message.content or ""
        tool_calls = self._extract_tool_calls(response)
        usage_info = self._extract_usage_info(response)

        if response.choices[0].finish_reason == "length" and not tool_calls:
            # Treat truncation without recoverable tool calls as fatal so the agent
            # backs off; if recovery already extracted a tool call from the truncated
            # text (Hermes brace-deficit case), continue with that and let the loop
            # produce feedback naturally on the next turn.
            raise OutputLengthExceededError(
                "Response truncated",
                truncated_response=content,
            )

        return ToolCallResponse(
            content=content,
            tool_calls=tool_calls,
            reasoning_content=getattr(message, "reasoning_content", None),
            usage=usage_info,
        )

    # ===== Handle LLM interaction (override parent) =====

    async def _handle_llm_interaction(
        self,
        chat: Chat,
        prompt: str,
        logging_paths: tuple[Path | None, Path | None, Path | None],
        original_instruction: str = "",
        session: TmuxSession | None = None,
    ) -> tuple[
        list[Command], bool, str, str, str, LLMResponse, MediaReadRequest | None
    ]:
        _, prompt_path, response_path = logging_paths

        if prompt_path is not None:
            prompt_path.write_text(prompt)

        messages = chat.messages.copy()
        messages.append({"role": "user", "content": prompt})

        try:
            start_time = time.time()
            tool_response = await self._call_llm_with_tools(messages)
            self._api_request_times.append((time.time() - start_time) * 1000)

            # After successful call, collapse any media that was perceived
            self._collapse_perceived_media(chat)

            assistant_message = {"role": "assistant", "content": tool_response.content}
            if tool_response.tool_calls:
                assistant_message["tool_calls"] = tool_response.tool_calls

            chat._messages.append({"role": "user", "content": prompt})
            chat._messages.append(assistant_message)

            if tool_response.tool_calls:
                for tc in tool_response.tool_calls:
                    chat._messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tc.get("id", ""),
                            "content": "executed",
                        }
                    )
                chat.reset_response_chain()

            if tool_response.usage:
                chat._cumulative_input_tokens += tool_response.usage.prompt_tokens
                chat._cumulative_output_tokens += tool_response.usage.completion_tokens
                chat._cumulative_cache_tokens += tool_response.usage.cache_tokens
                chat._cumulative_cost += tool_response.usage.cost_usd

        except ContextLengthExceededError:
            if not self._enable_summarize:
                raise
            if session is None:
                raise RuntimeError("Cannot handle context length error without session")

            self._unwind_messages_to_free_tokens(chat, target_free_tokens=4000)
            summary_prompt = None
            try:
                summary_prompt, subagent_refs = await self._with_block_timeout(
                    self._summarize(chat, original_instruction, session)
                )
                self._pending_subagent_refs = subagent_refs
                self._pending_handoff_prompt = summary_prompt
            except Exception:
                pass
            if summary_prompt is None:
                screen = await self._with_block_timeout(
                    session.capture_pane(capture_entire=False)
                )
                summary_prompt = (
                    f"{original_instruction}\n\nCurrent state: {(screen or '')[-1000:]}"
                )

            messages = chat.messages.copy()
            messages.append({"role": "user", "content": summary_prompt})
            start_time = time.time()
            tool_response = await self._call_llm_with_tools(messages)
            self._api_request_times.append((time.time() - start_time) * 1000)

            assistant_message = {"role": "assistant", "content": tool_response.content}
            if tool_response.tool_calls:
                assistant_message["tool_calls"] = tool_response.tool_calls
            chat._messages.append({"role": "user", "content": summary_prompt})
            chat._messages.append(assistant_message)
            if tool_response.tool_calls:
                for tc in tool_response.tool_calls:
                    chat._messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tc.get("id", ""),
                            "content": "executed",
                        }
                    )
                chat.reset_response_chain()
            if tool_response.usage:
                chat._cumulative_input_tokens += tool_response.usage.prompt_tokens
                chat._cumulative_output_tokens += tool_response.usage.completion_tokens
                chat._cumulative_cache_tokens += tool_response.usage.cache_tokens
                chat._cumulative_cost += tool_response.usage.cost_usd

        except BadRequestError as e:
            # API rejected the request — likely due to corrupt/oversized media.
            # Remove any multimodal content from chat history and tell the agent.
            self._collapse_perceived_media(chat)
            error_msg = str(e)
            self.logger.warning(f"BadRequestError (likely media issue): {error_msg}")

            # Return a feedback error so the agent can adapt (e.g., re-encode the file)
            chat._messages.append({"role": "user", "content": prompt})
            chat._messages.append(
                {"role": "assistant", "content": "[media rejected by API]"}
            )
            chat.reset_response_chain()

            llm_response = LLMResponse(content="[media rejected by API]")
            commands: list[Command] = []
            feedback = (
                f"ERROR: The API rejected the media content. The error was: {error_msg[:300]}. "
                f"The media file may be corrupted, too large, or in an unsupported format. "
                f"Try re-encoding with ffmpeg (e.g., ffmpeg -i input.mp4 -c:v libx264 -c:a aac output.mp4) "
                f"or extract a shorter segment."
            )
            return commands, False, feedback, "", "", llm_response, None

        except OutputLengthExceededError:
            chat._messages.extend(
                [
                    {"role": "user", "content": prompt},
                    {"role": "assistant", "content": "[truncated]"},
                    {
                        "role": "user",
                        "content": "ERROR!! Response truncated. Provide a shorter response.",
                    },
                ]
            )
            chat.reset_response_chain()
            messages = chat.messages.copy()
            start_time = time.time()
            tool_response = await self._call_llm_with_tools(messages)
            self._api_request_times.append((time.time() - start_time) * 1000)
            assistant_message = {"role": "assistant", "content": tool_response.content}
            if tool_response.tool_calls:
                assistant_message["tool_calls"] = tool_response.tool_calls
            chat._messages.append(assistant_message)
            if tool_response.tool_calls:
                for tc in tool_response.tool_calls:
                    chat._messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tc.get("id", ""),
                            "content": "executed",
                        }
                    )
                chat.reset_response_chain()
            if tool_response.usage:
                chat._cumulative_input_tokens += tool_response.usage.prompt_tokens
                chat._cumulative_output_tokens += tool_response.usage.completion_tokens
                chat._cumulative_cache_tokens += tool_response.usage.cache_tokens
                chat._cumulative_cost += tool_response.usage.cost_usd

        if response_path is not None:
            response_path.write_text(
                f"Content: {tool_response.content or ''}\n\n"
                f"Tool Calls: {json.dumps(tool_response.tool_calls, indent=2)}"
            )

        commands, is_task_complete, feedback, analysis, plan, media_read = (
            self._parse_tool_calls(tool_response.tool_calls)
        )

        llm_response = LLMResponse(
            content=tool_response.content or "",
            reasoning_content=tool_response.reasoning_content,
            usage=tool_response.usage,
        )

        return (
            commands,
            is_task_complete,
            feedback,
            analysis,
            plan,
            llm_response,
            media_read,
        )

    # ===== Agent loop (override parent) =====

    async def _run_agent_loop(
        self,
        initial_prompt: str,
        chat: Chat,
        logging_dir: Path | None = None,
        original_instruction: str = "",
    ) -> int:
        if self._context is None:
            raise RuntimeError("Agent context is not set.")
        if self._session is None:
            raise RuntimeError("Session is not set.")

        prompt = initial_prompt
        self._context.n_input_tokens = 0
        self._context.n_output_tokens = 0
        self._context.n_cache_tokens = 0
        self._context.cost_usd = None

        for episode in range(self._max_episodes):
            self._n_episodes = episode + 1
            if not await self._with_block_timeout(self._session.is_session_alive()):
                return episode + 1

            if original_instruction and self._enable_summarize:
                result = await self._with_block_timeout(
                    self._check_proactive_summarization(
                        chat, original_instruction, self._session
                    )
                )
                if result:
                    prompt, subagent_refs = result
                    self._pending_subagent_refs = subagent_refs
                    self._pending_handoff_prompt = prompt

            logging_paths = self._setup_episode_logging(logging_dir, episode)
            tokens_before_input = chat.total_input_tokens
            tokens_before_output = chat.total_output_tokens
            tokens_before_cache = chat.total_cache_tokens
            cost_before = chat.total_cost

            (
                commands,
                is_task_complete,
                feedback,
                analysis,
                plan,
                llm_response,
                media_read,
            ) = await self._handle_llm_interaction(
                chat, prompt, logging_paths, original_instruction, self._session
            )

            # Handle subagent refs from summarization
            if self._pending_subagent_refs:
                self._trajectory_steps.append(
                    Step(
                        step_id=len(self._trajectory_steps) + 1,
                        timestamp=datetime.now(timezone.utc).isoformat(),
                        source="system",
                        message="Context summarization performed.",
                        observation=Observation(
                            results=[
                                ObservationResult(
                                    subagent_trajectory_ref=self._pending_subagent_refs
                                )
                            ]
                        ),
                    )
                )
                self._pending_subagent_refs = None
            if self._pending_handoff_prompt:
                if self._linear_history:
                    self._split_trajectory_on_summarization(
                        self._pending_handoff_prompt
                    )
                else:
                    self._trajectory_steps.append(
                        Step(
                            step_id=len(self._trajectory_steps) + 1,
                            timestamp=datetime.now(timezone.utc).isoformat(),
                            source="user",
                            message=self._pending_handoff_prompt,
                        )
                    )
                self._pending_handoff_prompt = None

            # Build trajectory message content
            if self._save_raw_content_in_trajectory:
                message_content = llm_response.content
            else:
                parts = []
                if analysis:
                    parts.append(f"Analysis: {analysis}")
                if plan:
                    parts.append(f"Plan: {plan}")
                message_content = "\n".join(parts) if parts else ""

            self._context.n_input_tokens = chat.total_input_tokens
            self._context.n_output_tokens = chat.total_output_tokens
            self._context.n_cache_tokens = chat.total_cache_tokens
            self._context.cost_usd = chat.total_cost if chat.total_cost > 0 else None

            self._record_asciinema_marker(
                f"Episode {episode}: {len(commands)} commands"
                + (f" ({media_read.modality})" if media_read else ""),
            )

            # Handle parsing errors
            if feedback and "ERROR:" in feedback:
                prompt = f"Previous response had errors:\n{feedback}\nProvide valid tool calls."
                cache_used = chat.total_cache_tokens - tokens_before_cache
                step_cost = chat.total_cost - cost_before
                self._trajectory_steps.append(
                    Step(
                        step_id=len(self._trajectory_steps) + 1,
                        timestamp=datetime.now(timezone.utc).isoformat(),
                        source="agent",
                        model_name=self._model_name,
                        message=llm_response.content,
                        reasoning_content=llm_response.reasoning_content,
                        observation=Observation(
                            results=[ObservationResult(content=prompt)]
                        ),
                        metrics=Metrics(
                            prompt_tokens=chat.total_input_tokens - tokens_before_input,
                            completion_tokens=chat.total_output_tokens
                            - tokens_before_output,
                            cached_tokens=cache_used if cache_used > 0 else None,
                            cost_usd=step_cost if step_cost > 0 else None,
                        ),
                    )
                )
                continue

            if media_read is not None:
                # ===== MEDIA PERCEPTION PATH =====
                # Inject media into conversation context (model perceives directly)
                observation = await self._inject_media_into_context(media_read, chat)
                # Force the model to analyze what it perceived on the next turn
                # (media tools will be temporarily unavailable)
                self._media_just_perceived = True

                was_pending_completion = self._pending_completion
                if is_task_complete:
                    if self._pending_completion:
                        pass  # already confirmed
                    else:
                        self._pending_completion = True
                        observation = self._get_completion_confirmation_message(
                            observation
                        )
                else:
                    self._pending_completion = False

                # Record trajectory
                tool_calls_list: list[ToolCall] = []
                if not self._save_raw_content_in_trajectory:
                    fn_name = {
                        "video": "watch_video",
                        "audio": "listen_audio",
                        "image": "view_image",
                    }.get(media_read.modality, "view_media")
                    tool_calls_list.append(
                        ToolCall(
                            tool_call_id=f"call_{episode}_{media_read.modality}",
                            function_name=fn_name,
                            arguments={"file_path": media_read.file_path},
                        )
                    )
                    if is_task_complete:
                        tool_calls_list.append(
                            ToolCall(
                                tool_call_id=f"call_{episode}_task_complete",
                                function_name="mark_task_complete",
                                arguments={},
                            )
                        )

                cache_used = chat.total_cache_tokens - tokens_before_cache
                step_cost = chat.total_cost - cost_before
                self._trajectory_steps.append(
                    Step(
                        step_id=len(self._trajectory_steps) + 1,
                        timestamp=datetime.now(timezone.utc).isoformat(),
                        source="agent",
                        model_name=self._model_name,
                        message=message_content,
                        reasoning_content=llm_response.reasoning_content,
                        tool_calls=tool_calls_list or None,
                        observation=Observation(
                            results=[ObservationResult(content=observation)]
                        ),
                        metrics=Metrics(
                            prompt_tokens=chat.total_input_tokens - tokens_before_input,
                            completion_tokens=chat.total_output_tokens
                            - tokens_before_output,
                            cached_tokens=cache_used if cache_used > 0 else None,
                            cost_usd=step_cost if step_cost > 0 else None,
                        ),
                    )
                )
                self._dump_trajectory()

                if is_task_complete and was_pending_completion:
                    return episode + 1
                prompt = observation

            else:
                # ===== COMMANDS PATH =====
                # Model produced an analysis/commands — re-enable media tools
                self._media_just_perceived = False
                _, terminal_output = await self._with_block_timeout(
                    self._execute_commands(commands, self._session)
                )

                was_pending_completion = self._pending_completion
                if is_task_complete:
                    if self._pending_completion:
                        observation = terminal_output
                    else:
                        self._pending_completion = True
                        observation = self._get_completion_confirmation_message(
                            terminal_output
                        )
                else:
                    self._pending_completion = False
                    if feedback and "WARNINGS:" in feedback:
                        observation = f"Warnings:\n{feedback}\n\n{self._limit_output_length(terminal_output)}"
                    else:
                        observation = self._limit_output_length(terminal_output)

                cache_used = chat.total_cache_tokens - tokens_before_cache
                step_cost = chat.total_cost - cost_before
                tool_calls_list = []
                if not self._save_raw_content_in_trajectory:
                    for i, cmd in enumerate(commands):
                        tool_calls_list.append(
                            ToolCall(
                                tool_call_id=f"call_{episode}_{i + 1}",
                                function_name="bash_command",
                                arguments={
                                    "keystrokes": cmd.keystrokes,
                                    "duration": cmd.duration_sec,
                                },
                            )
                        )
                    if is_task_complete:
                        tool_calls_list.append(
                            ToolCall(
                                tool_call_id=f"call_{episode}_task_complete",
                                function_name="mark_task_complete",
                                arguments={},
                            )
                        )

                self._trajectory_steps.append(
                    Step(
                        step_id=len(self._trajectory_steps) + 1,
                        timestamp=datetime.now(timezone.utc).isoformat(),
                        source="agent",
                        model_name=self._model_name,
                        message=message_content,
                        reasoning_content=llm_response.reasoning_content,
                        tool_calls=tool_calls_list or None,
                        observation=Observation(
                            results=[ObservationResult(content=observation)]
                        ),
                        metrics=Metrics(
                            prompt_tokens=chat.total_input_tokens - tokens_before_input,
                            completion_tokens=chat.total_output_tokens
                            - tokens_before_output,
                            cached_tokens=cache_used if cache_used > 0 else None,
                            cost_usd=step_cost if step_cost > 0 else None,
                        ),
                    )
                )
                self._dump_trajectory()

                if is_task_complete and was_pending_completion:
                    return episode + 1
                prompt = observation

        return self._n_episodes
