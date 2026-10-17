"""
Jito Tip Dynamics & Overhead Modeling Engine.
Enforces Guardrail 6: Maximum entry tip cap of 1.5% notional and 0.005 SOL emergency exit budget.
"""

from dataclasses import dataclass
from typing import Tuple


@dataclass
class TipTierResult:
    base_tx_fee_sol: float
    priority_fee_sol: float
    jito_tip_sol: float
    total_overhead_sol: float
    overhead_pct_of_notional: float
    is_entry_tip_capped: bool  # True if <= 1.5% notional cap


class JitoTipModel:
    """
    Calculates dynamic priority fees and Jito bundle tips across normal and stressed market conditions.
    """

    BASE_TX_FEE_SOL = 0.000005
    MAX_ENTRY_TIP_NOTIONAL_RATIO = 0.015  # 1.5% of position notional
    EMERGENCY_EXIT_BUDGET_SOL = 0.005     # 0.005 SOL (~$0.80) exit budget

    def __init__(self, sol_price_usd: float = 155.0):
        self.sol_price_usd = sol_price_usd

    def calculate_entry_overhead(
        self,
        position_notional_usd: float,
        p50_tip_sol: float = 0.0010,
        p50_priority_fee_sol: float = 0.0001,
    ) -> TipTierResult:
        """
        Calculates overhead for an entry transaction leg, verifying the 1.5% tip cap.
        """
        position_sol = position_notional_usd / self.sol_price_usd
        max_tip_allowed = position_sol * self.MAX_ENTRY_TIP_NOTIONAL_RATIO

        # Enforce cap: if required tip > cap, flag it
        is_capped = (p50_tip_sol <= max_tip_allowed)
        applied_tip = min(p50_tip_sol, max_tip_allowed)

        total_overhead = self.BASE_TX_FEE_SOL + p50_priority_fee_sol + applied_tip
        overhead_pct = (total_overhead / position_sol) * 100.0 if position_sol > 0 else 0.0

        return TipTierResult(
            base_tx_fee_sol=self.BASE_TX_FEE_SOL,
            priority_fee_sol=p50_priority_fee_sol,
            jito_tip_sol=applied_tip,
            total_overhead_sol=round(total_overhead, 7),
            overhead_pct_of_notional=round(overhead_pct, 4),
            is_entry_tip_capped=is_capped,
        )

    def calculate_exit_overhead(
        self,
        position_notional_usd: float,
        is_emergency: bool = False,
        p50_tip_sol: float = 0.0010,
        p50_priority_fee_sol: float = 0.0001,
    ) -> TipTierResult:
        """
        Calculates overhead for an exit leg. Exits have access to the 0.005 SOL emergency budget.
        """
        position_sol = position_notional_usd / self.sol_price_usd
        applied_tip = self.EMERGENCY_EXIT_BUDGET_SOL if is_emergency else p50_tip_sol
        applied_priority = 0.0003 if is_emergency else p50_priority_fee_sol

        total_overhead = self.BASE_TX_FEE_SOL + applied_priority + applied_tip
        overhead_pct = (total_overhead / position_sol) * 100.0 if position_sol > 0 else 0.0

        return TipTierResult(
            base_tx_fee_sol=self.BASE_TX_FEE_SOL,
            priority_fee_sol=applied_priority,
            jito_tip_sol=applied_tip,
            total_overhead_sol=round(total_overhead, 7),
            overhead_pct_of_notional=round(overhead_pct, 4),
            is_entry_tip_capped=True,  # Exit legs are exempt from entry tip cap
        )

    def calculate_roundtrip_overhead(
        self,
        position_notional_usd: float,
        p50_tip_sol: float = 0.0010,
        p75_tip_sol: float = 0.0025,
        priority_p50_sol: float = 0.0001,
        priority_p75_sol: float = 0.0002,
    ) -> Tuple[float, float]:
        """
        Calculates (expected_roundtrip_overhead_pct, stressed_roundtrip_overhead_pct).
        Path A+ has strictly 2 transaction legs: 1 entry + 1 exit.
        """
        entry_exp = self.calculate_entry_overhead(position_notional_usd, p50_tip_sol, priority_p50_sol)
        exit_exp = self.calculate_exit_overhead(position_notional_usd, False, p50_tip_sol, priority_p50_sol)
        expected_overhead_pct = entry_exp.overhead_pct_of_notional + exit_exp.overhead_pct_of_notional

        # Stressed overhead uses p75 tip and priority fee for the planned 2 legs (entry + exit)
        # per bot.md (v2.5) Section 3.6: sum_{i=1}^2 TxOverhead_{i,p75}
        position_sol = position_notional_usd / self.sol_price_usd
        max_entry_tip = position_sol * self.MAX_ENTRY_TIP_NOTIONAL_RATIO
        entry_stress = self.BASE_TX_FEE_SOL + priority_p75_sol + min(p75_tip_sol, max_entry_tip)
        exit_stress = self.BASE_TX_FEE_SOL + priority_p75_sol + p75_tip_sol
        stressed_overhead_pct = ((entry_stress + exit_stress) / position_sol) * 100.0

        return round(expected_overhead_pct, 4), round(stressed_overhead_pct, 4)
