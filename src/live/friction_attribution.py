"""
Real-World Slippage & MEV Friction Attribution Engine.
Decomposes execution performance into theoretical alpha, venue fees,
slippage drag, Jito tips, and sandwich attack diagnostics.
"""

from dataclasses import asdict, dataclass, field
import logging
import math
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("FrictionAttribution")


@dataclass
class FrictionAttributionReport:
    """Detailed decomposition of a trade's performance into constituent friction components."""

    position_id: str
    token_mint: str
    gross_alpha_pct: float
    venue_fee_pct: float
    slippage_drag_pct: float
    mev_tip_pct: float
    priority_fee_pct: float
    total_friction_pct: float
    net_realized_alpha_pct: float
    entry_slippage_pct: float
    exit_slippage_pct: float
    total_slippage_pct: float
    mev_sandwich_detected: bool
    sandwich_confidence: float
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class SlippageAttributionEngine:
    """
    Quantifies execution drag across constant-product AMMs and detects MEV sandwiching.
    """

    DEFAULT_VENUE_FEE_PCT = 0.25  # 25 bps canonical PumpSwap fee

    def __init__(self, default_venue_fee_pct: float = DEFAULT_VENUE_FEE_PCT):
        self.default_venue_fee_pct = default_venue_fee_pct

    @staticmethod
    def calculate_entry_slippage(quoted_price_sol: float, actual_fill_price_sol: float) -> float:
        """
        Calculates one-way buy slippage percentage.
        Positive value indicates adverse price increase (paid more than quoted).
        """
        if quoted_price_sol <= 0.0:
            return 0.0
        return ((actual_fill_price_sol - quoted_price_sol) / quoted_price_sol) * 100.0

    @staticmethod
    def calculate_exit_slippage(quoted_price_sol: float, actual_fill_price_sol: float) -> float:
        """
        Calculates one-way sell slippage percentage.
        Positive value indicates adverse price decrease (received less than quoted).
        """
        if quoted_price_sol <= 0.0:
            return 0.0
        return ((quoted_price_sol - actual_fill_price_sol) / quoted_price_sol) * 100.0

    @staticmethod
    def calculate_theoretical_price_impact(
        trade_amount_sol: float,
        sol_reserve: float,
        token_reserve: float,
    ) -> float:
        """
        Calculates theoretical price impact on a constant-product AMM:
        Price Impact = dx / (x + dx)
        """
        if sol_reserve <= 0.0 or trade_amount_sol <= 0.0:
            return 0.0
        return (trade_amount_sol / (sol_reserve + trade_amount_sol)) * 100.0

    def detect_mev_sandwich(
        self,
        entry_slippage_pct: float,
        expected_price_impact_pct: float,
        pre_swap_price_sol: Optional[float] = None,
        immediate_post_block_price_sol: Optional[float] = None,
        max_acceptable_slippage_pct: float = 2.0,
    ) -> Tuple[bool, float, str]:
        """
        Detects whether an entry trade was victim to an MEV sandwich attack:
        1. Entry fill price suffered extreme slippage (> 2x theoretical price impact + tolerance).
        2. Immediate block price sharply reverted downwards back to original reserves.
        Returns: (is_sandwich_detected, confidence_score [0.0 - 1.0], reason_text).
        """
        # Criteria 1: Excessive adverse slippage over theoretical impact
        slippage_excess = entry_slippage_pct - expected_price_impact_pct
        if slippage_excess <= max_acceptable_slippage_pct:
            return False, 0.0, "Slippage within normal liquidity curve variance."

        confidence = 0.5  # Base suspicion from slippage anomaly alone

        # Criteria 2: Post-block reversal observation
        if pre_swap_price_sol and immediate_post_block_price_sol:
            # If price spiked high at fill but immediately crashed back down:
            reversion = (pre_swap_price_sol - immediate_post_block_price_sol) / pre_swap_price_sol
            if reversion > 0.03:  # >3% instant reversion
                confidence = 0.95
                return True, confidence, f"Severe sandwich pattern: {entry_slippage_pct:.2f}% entry spike followed by {reversion*100.0:.2f}% block reversion."
            elif reversion > 0.01:
                confidence = 0.75
                return True, confidence, f"Probable sandwich: {entry_slippage_pct:.2f}% entry spike with {reversion*100.0:.2f}% block reversion."

        # If slippage excess is extreme (>4.0% above theoretical)
        if slippage_excess >= 4.0:
            confidence = 0.80
            return True, confidence, f"High slippage anomaly ({entry_slippage_pct:.2f}% vs expected {expected_price_impact_pct:.2f}%)."

        return False, confidence, "Elevated slippage without conclusive reversion signature."

    def attribute_trade_friction(
        self,
        position_id: str,
        token_mint: str,
        quoted_entry_price_sol: float,
        actual_entry_fill_price_sol: float,
        quoted_exit_price_sol: float,
        actual_exit_fill_price_sol: float,
        sol_spent: float,
        actual_venue_fee_sol: float,
        actual_priority_fee_sol: float,
        actual_jito_tip_sol: float,
        sol_reserve: float = 50.0,
        token_reserve: float = 1_000_000.0,
        immediate_post_block_price_sol: Optional[float] = None,
    ) -> FrictionAttributionReport:
        """
        Computes complete friction breakdown for a round-trip live trade.
        """
        entry_slip = self.calculate_entry_slippage(quoted_entry_price_sol, actual_entry_fill_price_sol)
        exit_slip = self.calculate_exit_slippage(quoted_exit_price_sol, actual_exit_fill_price_sol)
        total_slip = entry_slip + exit_slip

        # Gross price movement between pre-trade quote and intended exit quote
        gross_alpha_pct = 0.0
        if quoted_entry_price_sol > 0:
            gross_alpha_pct = ((quoted_exit_price_sol - quoted_entry_price_sol) / quoted_entry_price_sol) * 100.0

        # Percentage costs relative to initial capital spent
        base_sol = max(sol_spent, 0.0001)
        venue_fee_pct = (actual_venue_fee_sol / base_sol) * 100.0 if actual_venue_fee_sol > 0 else (self.default_venue_fee_pct * 2)
        priority_fee_pct = (actual_priority_fee_sol / base_sol) * 100.0
        mev_tip_pct = (actual_jito_tip_sol / base_sol) * 100.0
        slippage_drag_pct = total_slip

        total_friction_pct = venue_fee_pct + priority_fee_pct + mev_tip_pct + slippage_drag_pct
        net_realized_alpha_pct = gross_alpha_pct - total_friction_pct

        # Check for MEV Sandwiching
        expected_impact = self.calculate_theoretical_price_impact(sol_spent, sol_reserve, token_reserve)
        is_sandwich, confidence, reason = self.detect_mev_sandwich(
            entry_slippage_pct=entry_slip,
            expected_price_impact_pct=expected_impact,
            pre_swap_price_sol=quoted_entry_price_sol,
            immediate_post_block_price_sol=immediate_post_block_price_sol,
        )

        return FrictionAttributionReport(
            position_id=position_id,
            token_mint=token_mint,
            gross_alpha_pct=round(gross_alpha_pct, 4),
            venue_fee_pct=round(venue_fee_pct, 4),
            slippage_drag_pct=round(slippage_drag_pct, 4),
            mev_tip_pct=round(mev_tip_pct, 4),
            priority_fee_pct=round(priority_fee_pct, 4),
            total_friction_pct=round(total_friction_pct, 4),
            net_realized_alpha_pct=round(net_realized_alpha_pct, 4),
            entry_slippage_pct=round(entry_slip, 4),
            exit_slippage_pct=round(exit_slip, 4),
            total_slippage_pct=round(total_slip, 4),
            mev_sandwich_detected=is_sandwich,
            sandwich_confidence=round(confidence, 2),
            details={
                "sandwich_reason": reason,
                "expected_price_impact_pct": round(expected_impact, 4),
                "sol_spent": sol_spent,
            },
        )
