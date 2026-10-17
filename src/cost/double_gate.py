"""
Pre-Flight Double Gate (Dual-Hurdle Cost Engine).
Enforces Guardrail 4: Expected cost <= 7.0%/6.0%, Stress cost <= 10.0%, and gross profit hurdles.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional
from src.cost.fee_tiers import FeeTierLookup, RegimeType
from src.cost.jito_tips import JitoTipModel
from src.cost.slippage_model import SlippageModel
from src.cost.failure_penalties import FailurePenaltyModel
from src.cluster.models import ClusterRiskScore


@dataclass
class DoubleGateResult:
    pool_address: str
    token_mint: str
    evaluated_ts: datetime
    regime: RegimeType
    fee_tier: int
    roundtrip_venue_fee_pct: float
    expected_impact_pct: float
    expected_overhead_pct: float
    expected_cost_pct: float
    stress_cost_pct: float
    ratchet_trigger_gross_pct: float
    net_profit_buffer_pct: float
    is_passed: bool
    rejection_reasons: List[str] = field(default_factory=list)


class PreFlightDoubleGate:
    """
    Evaluates empirical trading candidates against mathematical friction bounds prior to opening a position.
    """

    # Gate Caps codified in bot.md (v2.5) Section 3.6
    REGIME_A_EXPECTED_CAP_PCT = 7.00
    REGIME_B_EXPECTED_CAP_PCT = 6.00
    MAX_STRESS_COST_CAP_PCT = 10.00

    # Net Profit Buffers
    REGIME_A_NET_BUFFER_PCT = 11.00  # Leaves 10% - 14% net buffer
    REGIME_B_NET_BUFFER_PCT = 9.00   # Leaves 8% - 10% net buffer

    def __init__(self, sol_price_usd: float = 155.0):
        self.sol_price_usd = sol_price_usd
        self.fee_lookup = FeeTierLookup()
        self.tip_model = JitoTipModel(sol_price_usd)
        self.slippage_model = SlippageModel(sol_price_usd)
        self.failure_model = FailurePenaltyModel(sol_price_usd)

    def evaluate(
        self,
        pool_address: str,
        token_mint: str,
        market_cap_sol: float,
        current_sol_reserve: float,
        position_notional_usd: float = 15.0,
        is_canonical: bool = True,
        cluster_score: Optional[ClusterRiskScore] = None,
    ) -> DoubleGateResult:
        """
        Executes the pre-flight dual-hurdle double gate check.
        """
        now = datetime.now(timezone.utc)
        rejection_reasons: List[str] = []

        # 1. Fee Tier Lookup
        tier_info = self.fee_lookup.get_tier(market_cap_sol, is_canonical=is_canonical)
        if not tier_info.is_enabled:
            rejection_reasons.append(f"Venue/Pair not enabled: {tier_info.regime.value}")

        # Roundtrip venue fees (2 swaps: entry + exit)
        roundtrip_venue_fee = tier_info.total_fee_pct * 2.0

        # 2. Slippage & Price Impact
        impact_info = self.slippage_model.estimate_impact(position_notional_usd, current_sol_reserve)
        if not impact_info.passes_depth_screen:
            rejection_reasons.append(
                f"Depth inadequate: {impact_info.entry_impact_pct:.2f}% entry impact (Cap < 2.0%)"
            )

        # 3. Transaction Overheads (2 legs: entry + exit)
        exp_overhead, stress_overhead = self.tip_model.calculate_roundtrip_overhead(position_notional_usd)

        # 4. Total Expected Cost
        expected_cost = roundtrip_venue_fee + impact_info.expected_roundtrip_impact_pct + exp_overhead

        # 5. Total Stressed Cost
        stress_penalty = self.failure_model.get_amortized_stress_penalty_pct(position_notional_usd)
        stress_cost = roundtrip_venue_fee + impact_info.stressed_roundtrip_impact_pct + stress_overhead + stress_penalty

        # Expected Cost Cap per Regime
        expected_cap = (
            self.REGIME_A_EXPECTED_CAP_PCT
            if tier_info.regime == RegimeType.REGIME_A
            else self.REGIME_B_EXPECTED_CAP_PCT
        )
        if expected_cost > expected_cap:
            rejection_reasons.append(
                f"Expected friction {expected_cost:.2f}% exceeds {tier_info.regime.value} cap ({expected_cap:.1f}%)"
            )

        # Stressed Cost Cap
        if stress_cost > self.MAX_STRESS_COST_CAP_PCT:
            rejection_reasons.append(
                f"Stressed cost {stress_cost:.2f}% exceeds hard safety ceiling ({self.MAX_STRESS_COST_CAP_PCT:.1f}%)"
            )

        # 6. Projected Ratchet Trigger (Trailing Activation)
        net_buffer = (
            self.REGIME_A_NET_BUFFER_PCT
            if tier_info.regime == RegimeType.REGIME_A
            else self.REGIME_B_NET_BUFFER_PCT
        )
        ratchet_trigger = stress_cost + net_buffer

        # 7. Cluster & Risk Gate (Phase 2 Integration)
        if cluster_score is not None and not cluster_score.is_approved:
            reasons_summary = "; ".join(cluster_score.rejection_reasons[:2])
            rejection_reasons.append(f"Phase 2 Cluster Risk Rejected: {reasons_summary}")

        is_passed = (len(rejection_reasons) == 0)

        return DoubleGateResult(
            pool_address=pool_address,
            token_mint=token_mint,
            evaluated_ts=now,
            regime=tier_info.regime,
            fee_tier=tier_info.tier,
            roundtrip_venue_fee_pct=round(roundtrip_venue_fee, 4),
            expected_impact_pct=round(impact_info.expected_roundtrip_impact_pct, 4),
            expected_overhead_pct=round(exp_overhead, 4),
            expected_cost_pct=round(expected_cost, 4),
            stress_cost_pct=round(stress_cost, 4),
            ratchet_trigger_gross_pct=round(ratchet_trigger, 2),
            net_profit_buffer_pct=net_buffer,
            is_passed=is_passed,
            rejection_reasons=rejection_reasons,
        )
