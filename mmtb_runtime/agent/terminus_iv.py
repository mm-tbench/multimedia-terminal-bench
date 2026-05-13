"""
Terminus-IV — image+video perception variant of Terminus-MM (no listen_audio).

Lineage: Terminus-2 → Terminus-KIRA → Terminus-MM → Terminus-IV

Perception profile:
    - view_image    : YES
    - watch_video   : YES (includes embedded audio in video tracks)
    - listen_audio  : no   ← the ablated tool

Use Terminus-IV to measure the marginal contribution of native audio-only
perception over image+video access. The Terminus-MM − Terminus-IV delta
isolates "audio-driven" task headroom (audio that arrives outside a video
container).

Implementation: thin subclass of TerminusMM. Overrides:

1. The tools list passed to the LLM (drops listen_audio).
2. The MIME-extension whitelist (image + video, no standalone audio —
   calls to an audio-only file are rejected with a clear instruction to
   render to a spectrogram image first).
3. The system prompt template.

All other behaviour — marker-based command polling, media-collapse-
after-perception, Anthropic prompt caching — is inherited unchanged.

Usage:
    harbor run -p datasets/mmtb-core/<task> \\
        --agent-import-path "mmtb_runtime.agent:TerminusIV" \\
        -m openrouter/google/gemini-3.1-pro-preview
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
# Filtered tools — drop listen_audio; keep view_image + watch_video.
# ---------------------------------------------------------------------------
_IV_KEEP = {"execute_commands", "task_complete", "view_image", "watch_video"}
IV_TOOLS = [t for t in _MM_TOOLS if t["function"]["name"] in _IV_KEEP]

# ---------------------------------------------------------------------------
# Image + video MIME map (no standalone audio).
# ---------------------------------------------------------------------------
IV_MIME_MAP = {
    ext: mime
    for ext, mime in _MM_MIME_MAP.items()
    if mime.startswith("image/") or mime.startswith("video/")
}


class TerminusIV(TerminusMM):
    """
    Terminus-IV: image+video perception harness (no listen_audio).

    Same orchestration as TerminusMM, but the LLM cannot directly perceive
    standalone audio. Audio inside a video container is still accessible
    via watch_video. Calls to an audio-only file via view_image or
    watch_video are rejected at the host-side validation layer.
    """

    @staticmethod
    def name() -> str:
        return "terminus-iv"

    def _get_prompt_template_path(self) -> Path:
        return Path(__file__).parent / "prompt-template-iv.txt"

    async def _load_media_from_container(
        self,
        file_path: str,
    ) -> tuple[str | None, str | None, str | None]:
        """Read an image or video file from the container.

        MIME-map check uses IV_MIME_MAP (image + video only). The error
        message tells the agent to render audio to a spectrogram image
        first if it tries to read a standalone audio file.
        """
        if self._session is None:
            return None, None, "Session is not set"

        ext = Path(file_path).suffix.lower()
        mime = IV_MIME_MAP.get(ext)
        if mime is None:
            return (
                None,
                None,
                (
                    f"Unsupported format '{ext}'. "
                    f"Terminus-IV perceives image and video only (no standalone "
                    f"audio) — supported: {', '.join(sorted(IV_MIME_MAP.keys()))}. "
                    f"For audio-only files, render to a spectrogram image first: "
                    f"`ffmpeg -i {file_path} -lavfi showspectrumpic spec.png` "
                    f"and inspect with view_image."
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

        # After media perception, temporarily remove media tools to force
        # the model to analyse what it perceived via execute_commands.
        if self._media_just_perceived:
            tools = [
                t
                for t in IV_TOOLS
                if t["function"]["name"] not in ("view_image", "watch_video")
            ]
        else:
            tools = IV_TOOLS

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
