"""Provider-aware max_tokens cap.

Novita's API rejects max_tokens > 16384 with a `BadRequestError`. The default
across the rest of our harnesses (Phase 3+ per PR #170) is 32768; this helper
clamps to 16384 only when the LiteLLM model handle starts with ``novita/``,
leaving all other providers unchanged so existing Phase 3/4 sweeps remain
methodologically identical.

Usage:
    from mmtb_runtime.agent._max_tokens import resolve_max_tokens
    completion_kwargs["max_tokens"] = resolve_max_tokens(model_handle)
"""

from __future__ import annotations

NOVITA_MAX_TOKENS = 16384  # Novita API hard cap
DEFAULT_MAX_TOKENS = 32768  # Phase 3+ default per PR #170


def resolve_max_tokens(model: str) -> int:
    """Return the max_tokens cap for ``model`` (LiteLLM ``provider/name``)."""
    if isinstance(model, str) and model.startswith("novita/"):
        return NOVITA_MAX_TOKENS
    return DEFAULT_MAX_TOKENS
