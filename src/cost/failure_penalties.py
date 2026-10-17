"""
Failure Penalty & Jito 5-Outcome Accounting Engine.
Encodes outcomes from bot.md (v2.5) Section 3.4 and calculates amortized failure drag.
"""

from dataclasses import dataclass
from enum import Enum


class BundleOutcome(str, Enum):
    LANDED_SUCCESS = "LANDED_SUCCESS"
    LANDED_REVERT = "LANDED_REVERT"
    NOT_LANDED_EXPIRED = "NOT_LANDED_EXPIRED"
    UNKNOWN_TIMEOUT = "UNKNOWN_TIMEOUT"
    REBROADCAST_EXCEPTION = "REBROADCAST_EXCEPTION"


@dataclass
class FailurePenaltyResult:
    outcome: BundleOutcome
    base_fee_charged: bool
    priority_fee_charged: bool
    tip_charged: bool
    total_penalty_sol: float
    amortized_penalty_pct: float  # Amortized into pre-flight stress cost


class FailurePenaltyModel:
    """
    Models execution failure drag across the 5 bundle outcomes.
    """

    AMORTIZED_FAILURE_DRAG_SOL = 0.0003  # ~0.0003 SOL amortized revert/expiry buffer
    BASE_TX_FEE_SOL = 0.000005

    def __init__(self, sol_price_usd: float = 155.0):
        self.sol_price_usd = sol_price_usd

    def evaluate_bundle_outcome(
        self,
        outcome: BundleOutcome,
        tip_sol: float,
        priority_fee_sol: float = 0.0001,
        position_notional_usd: float = 15.0,
    ) -> FailurePenaltyResult:
        """
        Calculates exact fees incurred based on bundle landing status.
        Crucial Invariant: If tip is placed post-swap in bundle, revert halts execution before tip!
        """
        position_sol = position_notional_usd / self.sol_price_usd

        if outcome == BundleOutcome.LANDED_SUCCESS:
            total_sol = self.BASE_TX_FEE_SOL + priority_fee_sol + tip_sol
            return FailurePenaltyResult(
                outcome=outcome,
                base_fee_charged=True,
                priority_fee_charged=True,
                tip_charged=True,
                total_penalty_sol=total_sol,
                amortized_penalty_pct=0.0,
            )
        elif outcome == BundleOutcome.LANDED_REVERT:
            # Tip instruction placed after swap is NOT charged on program revert
            total_sol = self.BASE_TX_FEE_SOL + priority_fee_sol
            penalty_pct = (total_sol / position_sol) * 100.0 if position_sol > 0 else 0.0
            return FailurePenaltyResult(
                outcome=outcome,
                base_fee_charged=True,
                priority_fee_charged=True,
                tip_charged=False,  # Spared by post-swap bundle placement invariant
                total_penalty_sol=total_sol,
                amortized_penalty_pct=penalty_pct,
            )
        elif outcome == BundleOutcome.NOT_LANDED_EXPIRED:
            return FailurePenaltyResult(
                outcome=outcome,
                base_fee_charged=False,
                priority_fee_charged=False,
                tip_charged=False,
                total_penalty_sol=0.0,
                amortized_penalty_pct=0.0,
            )
        else:
            # Timeout / Exception
            total_sol = self.BASE_TX_FEE_SOL + priority_fee_sol
            penalty_pct = (total_sol / position_sol) * 100.0 if position_sol > 0 else 0.0
            return FailurePenaltyResult(
                outcome=outcome,
                base_fee_charged=True,
                priority_fee_charged=True,
                tip_charged=False,
                total_penalty_sol=total_sol,
                amortized_penalty_pct=penalty_pct,
            )

    def get_amortized_stress_penalty_pct(self, position_notional_usd: float) -> float:
        """Returns amortized failure penalty in % of notional for pre-flight stress gate."""
        position_sol = position_notional_usd / self.sol_price_usd
        if position_sol <= 0:
            return 0.50
        return (self.AMORTIZED_FAILURE_DRAG_SOL / position_sol) * 100.0
