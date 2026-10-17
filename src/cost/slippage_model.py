"""
Price Impact & Slippage Modeling Engine.
Estimates entry and exit price impact for target position sizes on constant-product PumpSwap pools.
"""

from dataclasses import dataclass


@dataclass
class ImpactResult:
    swap_size_sol: float
    pool_sol_reserve: float
    entry_impact_pct: float
    exit_impact_pct: float
    expected_roundtrip_impact_pct: float
    stressed_roundtrip_impact_pct: float  # Includes +1.0% adverse slippage buffer
    passes_depth_screen: bool  # True if entry impact < 2.0%


class SlippageModel:
    """
    Constant-product AMM slippage and market impact estimator.
    """

    ADVERSE_SLIPPAGE_BUFFER_PCT = 1.00  # 1.0% adverse slippage buffer in stress model

    def __init__(self, sol_price_usd: float = 155.0):
        self.sol_price_usd = sol_price_usd

    def estimate_impact(
        self,
        position_notional_usd: float,
        current_sol_reserve: float,
    ) -> ImpactResult:
        """
        Calculates expected and stressed price impact for a position.
        Formula: impact ≈ swap_size / (2 * reserve) * 100
        """
        if current_sol_reserve <= 0:
            return ImpactResult(
                swap_size_sol=0.0,
                pool_sol_reserve=0.0,
                entry_impact_pct=99.0,
                exit_impact_pct=99.0,
                expected_roundtrip_impact_pct=99.0,
                stressed_roundtrip_impact_pct=99.0,
                passes_depth_screen=False,
            )

        swap_size_sol = position_notional_usd / self.sol_price_usd
        entry_impact = (swap_size_sol / (2.0 * current_sol_reserve)) * 100.0
        # Exit impact models a slightly larger reserve or price run
        exit_impact = entry_impact * 1.05

        expected_roundtrip = entry_impact + exit_impact
        stressed_roundtrip = expected_roundtrip + self.ADVERSE_SLIPPAGE_BUFFER_PCT

        passes_depth = entry_impact < 2.0

        return ImpactResult(
            swap_size_sol=round(swap_size_sol, 5),
            pool_sol_reserve=current_sol_reserve,
            entry_impact_pct=round(entry_impact, 4),
            exit_impact_pct=round(exit_impact, 4),
            expected_roundtrip_impact_pct=round(expected_roundtrip, 4),
            stressed_roundtrip_impact_pct=round(stressed_roundtrip, 4),
            passes_depth_screen=passes_depth,
        )
