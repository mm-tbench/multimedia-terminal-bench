"""
Terminus-IA — image+audio perception variant of Terminus-MM (no watch_video).

Lineage: Terminus-2 → Terminus-KIRA → Terminus-MM → Terminus-IA

Perception profile:
    - view_image    : YES
    - listen_audio  : YES
    - watch_video   : no   ← the ablated tool

Use Terminus-IA to measure the marginal contribution of native video
perception over image+audio access. The Terminus-MM − Terminus-IA delta
isolates "video-driven" task headroom.

Implementation: thin subclass of TerminusMM. Overrides:

1. The tools list passed to the LLM (drops watch_video).
2. The MIME-extension whitelist (image + audio, no video — calls to a
   video file are rejected with a clear instruction to extract frames /
   audio first).
3. The system prompt template.

All other behaviour — marker-based command polling, media-collapse-
after-perception, Anthropic prompt caching — is inherited unchanged.

Usage:
    harbor run -p datasets/mmtb-core/<task> \\
        --agent-import-path "mmtb_runtime.agent:TerminusIA" \\
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
# Filtered tools — drop watch_video; keep view_image + listen_audio.
# ---------------------------------------------------------------------------
_IA_KEEP = {"execute_commands", "task_complete", "view_image", "listen_audio"}
IA_TOOLS = [t for t in _MM_TOOLS if t["function"]["name"] in _IA_KEEP]

# ---------------------------------------------------------------------------
# Image + audio MIME map (no video).
# ---------------------------------------------------------------------------
IA_MIME_MAP = {
    ext: mime
    for ext, mime in _MM_MIME_MAP.items()
    if mime.startswith("image/") or mime.startswith("audio/")
}


class TerminusIA(TerminusMM):
    """
    Terminus-IA: image+audio perception harness (no watch_video).

    Same orchestration as TerminusMM, but the LLM cannot directly perceive
    video. Calls to a video file via view_image or listen_audio are rejected
    at the host-side validation layer.
    """

    @staticmethod
    def name() -> str:
        return "terminus-ia"

    def _get_prompt_template_path(self) -> Path:
        return Path(__file__).parent / "prompt-template-ia.txt"

    async def _load_media_from_container(
        self,
        file_path: str,
    ) -> tuple[str | None, str | None, str | None]:
        """Read an image or audio file from the container.

        MIME-map check uses IA_MIME_MAP (image + audio only). The error
        message tells the agent to extract frames or audio first if it
        tries to read a video file.
        """
        if self._session is None:
            return None, None, "Session is not set"

        ext = Path(file_path).suffix.lower()
        mime = IA_MIME_MAP.get(ext)
        if mime is None:
            return (
                None,
                None,
                (
                    f"Unsupported format '{ext}'. "
                    f"Terminus-IA perceives image and audio only — "
                    f"supported: {', '.join(sorted(IA_MIME_MAP.keys()))}. "
                    f"For video files, extract frames or audio first: "
                    f"`ffmpeg -i {file_path} -vf fps=1 frame_%04d.png` "
                    f"or `ffmpeg -i {file_path} -vn -ac 1 -ar 16000 audio.wav`"
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
                for t in IA_TOOLS
                if t["function"]["name"] not in ("view_image", "listen_audio")
            ]
        else:
            tools = IA_TOOLS

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
