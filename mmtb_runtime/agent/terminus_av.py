"""
Terminus-AV — audio+video perception variant of Terminus-MM.

Lineage: Terminus-2 → Terminus-KIRA → Terminus-MM → Terminus-AV

Perception profile:
    - watch_video   : YES
    - listen_audio  : YES
    - view_image    : no   ← the ablated tool

Use Terminus-AV to measure the marginal contribution of native image
perception over audio+video access. The Terminus-MM − Terminus-AV delta
isolates "image-driven" task headroom (visual content that arrives outside
a video container — photos, diagrams, screenshots).

Implementation: thin subclass of TerminusMM. Overrides three things:

1. The tools list passed to the LLM (drops view_image).
2. The MIME-extension whitelist (audio + video, no image).
3. The system prompt template.

All other behaviour — marker-based command polling, media-collapse-after-
perception, Anthropic prompt caching — is inherited unchanged.

Usage:
    harbor run -p datasets/mmtb-core/<task> \\
        --agent-import-path "mmtb_runtime.agent:TerminusAV" \\
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
# Filtered tools — drop view_image; keep listen_audio + watch_video.
# ---------------------------------------------------------------------------
_AV_KEEP = {"execute_commands", "task_complete", "listen_audio", "watch_video"}
AV_TOOLS = [t for t in _MM_TOOLS if t["function"]["name"] in _AV_KEEP]

# ---------------------------------------------------------------------------
# Audio + video MIME map (no image).
# ---------------------------------------------------------------------------
AV_MIME_MAP = {
    ext: mime
    for ext, mime in _MM_MIME_MAP.items()
    if mime.startswith("audio/") or mime.startswith("video/")
}


class TerminusAV(TerminusMM):
    """
    Terminus-AV: audio+video perception harness (no view_image).

    Same orchestration as TerminusMM, but the LLM cannot directly perceive
    standalone images. Calls to view_image on image files are rejected at
    the host-side validation layer.
    """

    @staticmethod
    def name() -> str:
        return "terminus-av"

    def _get_prompt_template_path(self) -> Path:
        return Path(__file__).parent / "prompt-template-av.txt"

    async def _load_media_from_container(
        self,
        file_path: str,
    ) -> tuple[str | None, str | None, str | None]:
        """Read an audio or video file from the container.

        MIME-map check uses AV_MIME_MAP (audio + video only). Calls to
        view_image are rejected because the tool itself is not in the
        schema; this guard catches any extension mismatch on the kept
        tools.
        """
        if self._session is None:
            return None, None, "Session is not set"

        ext = Path(file_path).suffix.lower()
        mime = AV_MIME_MAP.get(ext)
        if mime is None:
            return (
                None,
                None,
                (
                    f"Unsupported format '{ext}'. "
                    f"Terminus-AV perceives audio and video only (no "
                    f"standalone images) — supported: "
                    f"{', '.join(sorted(AV_MIME_MAP.keys()))}. "
                    f"Image files cannot be perceived natively on this harness."
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
                for t in AV_TOOLS
                if t["function"]["name"] not in ("listen_audio", "watch_video")
            ]
        else:
            tools = AV_TOOLS

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
