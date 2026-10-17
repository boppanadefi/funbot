"""
Canonical PumpSwap Dynamic Tiered Fee Lookup Engine.
Evaluates on-chain swap fee based on token total 1B fixed market cap in SOL.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class RegimeType(str, Enum):
    REGIME_A = "REGIME_A"  # Tier 1: 0 - 420 SOL MC (Early Momentum)
    REGIME_B = "REGIME_B"  # Tier 2+: >= 420 SOL MC (Continuation & Expansion)
    REGIME_C = "REGIME_C"  # Non-canonical pools (Disabled by default)


@dataclass
class FeeTierResult:
    tier: int
    market_cap_sol: float
    total_fee_pct: float
    creator_fee_pct: float
    protocol_fee_pct: float
    lp_fee_pct: float
    regime: RegimeType
    is_canonical: bool
    is_enabled: bool


class FeeTierLookup:
    """
    Implements the official canonical PumpSwap fee schedule from bot.md (v2.5) Section 2.1.
    """

    @staticmethod
    def get_tier(market_cap_sol: float, is_canonical: bool = True) -> FeeTierResult:
        if not is_canonical:
            return FeeTierResult(
                tier=0,
                market_cap_sol=market_cap_sol,
                total_fee_pct=0.300,
                creator_fee_pct=0.000,
                protocol_fee_pct=0.050,
                lp_fee_pct=0.250,
                regime=RegimeType.REGIME_C,
                is_canonical=False,
                is_enabled=False,  # Disabled by default per Guardrail specification
            )

        # Canonical SOL pools
        if market_cap_sol < 420.0:
            return FeeTierResult(
                tier=1,
                market_cap_sol=market_cap_sol,
                total_fee_pct=1.250,
                creator_fee_pct=0.300,
                protocol_fee_pct=0.930,
                lp_fee_pct=0.020,
                regime=RegimeType.REGIME_A,
                is_canonical=True,
                is_enabled=True,
            )
        elif market_cap_sol < 1470.0:
            return FeeTierResult(
                tier=2,
                market_cap_sol=market_cap_sol,
                total_fee_pct=1.200,
                creator_fee_pct=0.950,
                protocol_fee_pct=0.050,
                lp_fee_pct=0.200,
                regime=RegimeType.REGIME_B,
                is_canonical=True,
                is_enabled=True,
            )
        elif market_cap_sol < 2460.0:
            return FeeTierResult(
                tier=3,
                market_cap_sol=market_cap_sol,
                total_fee_pct=1.150,
                creator_fee_pct=0.900,
                protocol_fee_pct=0.050,
                lp_fee_pct=0.200,
                regime=RegimeType.REGIME_B,
                is_canonical=True,
                is_enabled=True,
            )
        elif market_cap_sol < 3440.0:
            return FeeTierResult(
                tier=4,
                market_cap_sol=market_cap_sol,
                total_fee_pct=1.100,
                creator_fee_pct=0.850,
                protocol_fee_pct=0.050,
                lp_fee_pct=0.200,
                regime=RegimeType.REGIME_B,
                is_canonical=True,
                is_enabled=True,
            )
        elif market_cap_sol < 4420.0:
            return FeeTierResult(
                tier=5,
                market_cap_sol=market_cap_sol,
                total_fee_pct=1.050,
                creator_fee_pct=0.800,
                protocol_fee_pct=0.050,
                lp_fee_pct=0.200,
                regime=RegimeType.REGIME_B,
                is_canonical=True,
                is_enabled=True,
            )
        else:
            # Tier 6: Market cap > 4420 SOL steps down towards 0.300%
            decay_fee = max(0.300, 1.050 - ((market_cap_sol - 4420.0) / 2000.0) * 0.750)
            return FeeTierResult(
                tier=6,
                market_cap_sol=market_cap_sol,
                total_fee_pct=round(decay_fee, 4),
                creator_fee_pct=round(decay_fee * 0.60, 4),
                protocol_fee_pct=0.050,
                lp_fee_pct=round(decay_fee * 0.35, 4),
                regime=RegimeType.REGIME_B,
                is_canonical=True,
                is_enabled=True,
            )
