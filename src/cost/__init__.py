"""
Dynamic Cost Engine & Pre-Flight Double Gate.
Enforces Guardrail 4 (Dual-Hurdle Double Gate) and Guardrail 6 (Jito Tip Caps).
"""

from src.cost.fee_tiers import (
    FeeTierResult,
    FeeTierLookup,
    RegimeType,
)
from src.cost.jito_tips import (
    JitoTipModel,
    TipTierResult,
)
from src.cost.slippage_model import (
    SlippageModel,
    ImpactResult,
)
from src.cost.failure_penalties import (
    FailurePenaltyModel,
    BundleOutcome,
)
from src.cost.double_gate import (
    PreFlightDoubleGate,
    DoubleGateResult,
)

__all__ = [
    "FeeTierResult",
    "FeeTierLookup",
    "RegimeType",
    "JitoTipModel",
    "TipTierResult",
    "SlippageModel",
    "ImpactResult",
    "FailurePenaltyModel",
    "BundleOutcome",
    "PreFlightDoubleGate",
    "DoubleGateResult",
]
