"""
Domain models for Cluster Analysis, Burner Detection, and Token Safety.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Set


@dataclass
class WalletProfile:
    pubkey: str
    pre_trade_tx_count: int
    first_seen_ts: Optional[datetime] = None
    creation_slot: Optional[int] = None
    total_sol_bought: float = 0.0

    @property
    def is_burner(self) -> bool:
        """Guardrail 5: Buyers with < 3 lifetime pre-trade transactions are fresh burners."""
        return self.pre_trade_tx_count < 3


@dataclass
class FundingHop:
    child_pubkey: str
    parent_pubkey: str
    amount_sol: float
    hop_level: int  # 1 = direct funder, 2 = funder's funder
    tx_signature: Optional[str] = None
    timestamp: Optional[datetime] = None


@dataclass
class SybilGroup:
    group_id: str
    wallets: List[str]
    slot: int
    mean_sol_size: float
    size_variance: float
    coordinated_tx_count: int


@dataclass
class SafetyCheckResult:
    token_mint: str
    mint_authority_revoked: bool
    freeze_authority_revoked: bool
    is_canonical_sol: bool
    is_safe: bool
    failure_reasons: List[str] = field(default_factory=list)


@dataclass
class ClusterRiskScore:
    pool_address: str
    token_mint: str
    evaluated_ts: datetime
    total_buyers: int
    burner_count: int
    burner_volume_ratio: float  # [0.0 - 1.0]
    top_parent_pubkey: Optional[str]
    top_parent_concentration: float  # [0.0 - 1.0]
    sybil_detected: bool
    safety: SafetyCheckResult
    composite_risk_score: float  # [0.0 - 1.0], 0.0 is perfect, 1.0 is maximum risk
    is_approved: bool  # True if passes all Guardrail 5 & 7 filters
    rejection_reasons: List[str] = field(default_factory=list)
    # Fix 4: Real-Time Order Flow Dynamics (VUW & Aggression Ratio)
    vuw: float = 0.0
    aggression_ratio: float = 0.0
    order_flow_passed: bool = True
    order_flow_classification: str = "ORGANIC_RETAIL"
