"""
Terminus-A — audio-only perception variant of Terminus-MM.

Lineage: Terminus-2 → Terminus-KIRA → Terminus-MM → Terminus-A

Perception profile:
    - listen_audio  : YES   (the only multimedia perception tool)
    - watch_video   : no
    - view_image    : no

Use Terminus-A as the audio-only baseline cell when isolating audio
contribution from joint audio-visual contribution. It is to "audio" what
Terminus-KIRA is to "image": the strict single-modality perception harness.

Implementation: thin subclass of TerminusMM. Overrides three things:

1. The tools list passed to the LLM (drops watch_video + view_image).
2. The MIME-extension whitelist (audio extensions only — calls to
   listen_audio on .mp4 etc. are rejected with a clear error message
   so the agent must extract audio with ffmpeg first).
3. The system prompt template.

All other behavior — marker-based command polling, media-collapse-after-
perception, Anthropic prompt caching, etc. — is inherited unchanged.

Usage:
    harbor run -p datasets/mmtb-core/<task> \\
        --agent-import-path "mmtb_runtime.agent:TerminusA" \\
        -m gemini/gemini-3.1-pro-preview
"""

from __future__ import annotations

from pathlib import Path

import litellm

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

# Import everything Terminus-A reuses from MM. We re-use the dataclasses,
# prompt fragments, the multimodal builder, and most of the orchestration.
from mmtb_runtime.agent.terminus_mm import (
    TOOLS as _MM_TOOLS,
    _MIME_MAP as _MM_MIME_MAP,
    TerminusMM,
    ToolCallResponse,
    ContextLengthExceededError,
    OutputLengthExceededError,
)
from mmtb_runtime.agent.anthropic_caching import add_anthropic_caching
from mmtb_runtime.agent._max_tokens import resolve_max_tokens


# ---------------------------------------------------------------------------
# Filtered tools — drop watch_video and view_image; keep listen_audio.
# ---------------------------------------------------------------------------
_A_KEEP = {"execute_commands", "task_complete", "listen_audio"}
A_TOOLS = [t for t in _MM_TOOLS if t["function"]["name"] in _A_KEEP]

# ---------------------------------------------------------------------------
# Audio-only MIME map. Reject calls to listen_audio on video / image files
# with a clear instruction to extract audio first via ffmpeg.
# ---------------------------------------------------------------------------
A_MIME_MAP = {
    ext: mime for ext, mime in _MM_MIME_MAP.items() if mime.startswith("audio/")
}


class TerminusA(TerminusMM):
    """
    Terminus-A: audio-only perception harness.

    Same orchestration as TerminusMM, but the LLM only sees `listen_audio`
    as a perception tool. Calls to listen_audio on non-audio files are
    rejected at the host-side validation layer.
    """

    @staticmethod
    def name() -> str:
        return "terminus-a"

    # ----- Override 1: prompt template -----
    def _get_prompt_template_path(self) -> Path:
        return Path(__file__).parent / "prompt-template-a.txt"

    # ----- Override 2: load_media_from_container — audio MIME whitelist -----
    async def _load_media_from_container(
        self,
        file_path: str,
    ) -> tuple[str | None, str | None, str | None]:
        """Read an audio file from the container.

        Identical to the parent's implementation except the MIME-map check
        uses A_MIME_MAP (audio extensions only). The error message tells
        the agent to extract audio first with ffmpeg if it tries to read a
        video / image file.
        """
        if self._session is None:
            return None, None, "Session is not set"

        ext = Path(file_path).suffix.lower()
        mime = A_MIME_MAP.get(ext)
        if mime is None:
            return (
                None,
                None,
                (
                    f"Unsupported format '{ext}' for listen_audio. "
                    f"Terminus-A is audio-only — "
                    f"supported: {', '.join(sorted(A_MIME_MAP.keys()))}. "
                    f"For video files, extract audio first with: "
                    f"ffmpeg -i {file_path} -vn -ac 1 -ar 16000 -y audio.wav"
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

    # ----- Override 3: _call_llm_with_tools — filtered tools -----
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
    async def _call_llm_with_tools(self, messages: list[dict]) -> ToolCallResponse:
        messages = add_anthropic_caching(messages, self._model_name)

        # After media perception, temporarily remove the audio tool to force
        # the model to analyze what it heard via execute_commands (which has
        # the analysis/plan fields). Without this, the model can endlessly
        # call listen_audio without ever writing its observations.
        if self._media_just_perceived:
            tools = [t for t in A_TOOLS if t["function"]["name"] != "listen_audio"]
        else:
            tools = A_TOOLS

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
            # Soft guard matches TerminusMM/TerminusKira: continue when recovery
            # already extracted a tool call from the truncated text; back off
            # cleanly only when there's nothing to act on.
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
