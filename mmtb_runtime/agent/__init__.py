"""MMTB agents — Terminus-KIRA (vendored) and the Terminus-MM perception family."""

from mmtb_runtime.agent.codex_oauth import CodexOAuth
from mmtb_runtime.agent.terminus_a import TerminusA
from mmtb_runtime.agent.terminus_av import TerminusAV
from mmtb_runtime.agent.terminus_ia import TerminusIA
from mmtb_runtime.agent.terminus_iv import TerminusIV
from mmtb_runtime.agent.terminus_kira import TerminusKira
from mmtb_runtime.agent.terminus_mm import TerminusMM
from mmtb_runtime.agent.terminus_v import TerminusV

# Alias preserved for callers that use the all-caps spelling.
TerminusKIRA = TerminusKira

__all__ = [
    "CodexOAuth",
    "TerminusA",
    "TerminusAV",
    "TerminusIA",
    "TerminusIV",
    "TerminusKIRA",
    "TerminusKira",
    "TerminusMM",
    "TerminusV",
]
