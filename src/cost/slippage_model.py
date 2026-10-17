"""
Price Impact, Slippage Modeling & Dynamic Liquidity-Based Sizing Engine.
Estimates entry and exit price impact for target position sizes on constant-product PumpSwap pools.

Fix 5: Dynamic Liquidity-Based Sizing:
- Calculates exact maximum safe swap in SOL and USD for a target impact (default 1.5%).
- Sizing Rule: Position Size = min(Calculated Max Swap, $25 Active Wallet Limit)
- Rejects pool if Calculated Max Swap < $3.00 (minimum viable to overcome fixed Solana tx overhead).
"""

from dataclasses import dataclass
from typing import Tuple


@dataclass
class ImpactResult:
    swap_size_sol: float
    pool_sol_reserve: float
    entry_impact_pct: float
    exit_impact_pct: float
    expected_roundtrip_impact_pct: float
    stressed_roundtrip_impact_pct: float  # Includes +1.0% adverse slippage buffer
    passes_depth_screen: bool  # True if entry impact < 2.0% or dynamically viable (>= $3.00)
    # Fix 5: Dynamic Sizing Metrics
    max_safe_swap_usd: float = 0.0
    dynamic_position_size_usd: float = 0.0
    is_dynamically_viable: bool = True


class SlippageModel:
    """
    Constant-product AMM slippage, market impact, and dynamic liquidity-based sizing estimator.
    """

    ADVERSE_SLIPPAGE_BUFFER_PCT = 1.00  # 1.0% adverse slippage buffer in stress model
    DEFAULT_MAX_ACCEPTABLE_IMPACT_PCT = 1.50  # 1.5% max acceptable one-way impact
    MIN_VIABLE_SWAP_USD = 3.00                # $3.00 minimum swap to overcome fixed tx overhead
    MAX_ACTIVE_WALLET_LIMIT_USD = 25.00       # $25.00 active trading wallet limit

    def __init__(self, sol_price_usd: float = 155.0):
        self.sol_price_usd = sol_price_usd

    def calculate_max_safe_swap_sol(
        self,
        current_sol_reserve: float,
        max_acceptable_impact_pct: float = 1.50,
    ) -> float:
        """
        Calculates the exact swap size in SOL that results in max_acceptable_impact_pct (1.5%) price impact.
        Formula:
            entry_impact_pct = (swap_size_sol / (2.0 * current_sol_reserve)) * 100.0
            swap_size_sol = (max_acceptable_impact_pct / 100.0) * (2.0 * current_sol_reserve)
        """
        if current_sol_reserve <= 0:
            return 0.0
        swap_sol = (max_acceptable_impact_pct / 100.0) * (2.0 * current_sol_reserve)
        return round(swap_sol, 6)

    def calculate_max_safe_swap_usd(
        self,
        current_sol_reserve: float,
        max_acceptable_impact_pct: float = 1.50,
    ) -> float:
        """Calculates the max safe swap notional in USD."""
        swap_sol = self.calculate_max_safe_swap_sol(current_sol_reserve, max_acceptable_impact_pct)
        return round(swap_sol * self.sol_price_usd, 2)

    def calculate_dynamic_position_size_usd(
        self,
        current_sol_reserve: float,
        max_acceptable_impact_pct: float = 1.50,
        max_wallet_limit_usd: float = 25.00,
        min_viable_swap_usd: float = 3.00,
    ) -> Tuple[float, bool, str]:
        """
        Fix 5 Dynamic Sizing Rule:
        Position Size = min(Calculated Max Swap, $25 Active Wallet Limit)
        If Calculated Max Swap < $3.00 (absolute minimum to overcome fixed Solana tx overhead),
        then hard reject the pool.
        """
        if current_sol_reserve <= 0:
            return 0.0, False, "AMM SOL reserve is zero or negative"

        max_safe_usd = self.calculate_max_safe_swap_usd(current_sol_reserve, max_acceptable_impact_pct)

        if max_safe_usd < min_viable_swap_usd:
            return (
                0.0,
                False,
                f"Calculated Max Swap ${max_safe_usd:.2f} < ${min_viable_swap_usd:.2f} minimum viable threshold "
                f"(pool cannot handle minimum size without exceeding {max_acceptable_impact_pct:.1f}% impact)",
            )

        position_size_usd = min(max_safe_usd, max_wallet_limit_usd)
        return (
            round(position_size_usd, 2),
            True,
            f"Dynamically sized to ${position_size_usd:.2f} (max safe swap ${max_safe_usd:.2f} at {max_acceptable_impact_pct:.1f}% impact)",
        )

    def estimate_impact(
        self,
        position_notional_usd: float,
        current_sol_reserve: float,
        max_acceptable_impact_pct: float = 1.50,
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
                max_safe_swap_usd=0.0,
                dynamic_position_size_usd=0.0,
                is_dynamically_viable=False,
            )

        swap_size_sol = position_notional_usd / self.sol_price_usd
        entry_impact = (swap_size_sol / (2.0 * current_sol_reserve)) * 100.0
        # Exit impact models a slightly larger reserve or price run
        exit_impact = entry_impact * 1.05

        expected_roundtrip = entry_impact + exit_impact
        stressed_roundtrip = expected_roundtrip + self.ADVERSE_SLIPPAGE_BUFFER_PCT

        max_safe_usd = self.calculate_max_safe_swap_usd(current_sol_reserve, max_acceptable_impact_pct)
        dyn_size_usd, is_viable, _ = self.calculate_dynamic_position_size_usd(
            current_sol_reserve, max_acceptable_impact_pct=max_acceptable_impact_pct
        )

        # Evaluated swap passes depth screen if its entry impact is < 2.0%
        passes_depth = (entry_impact < 2.0)

        return ImpactResult(
            swap_size_sol=round(swap_size_sol, 5),
            pool_sol_reserve=current_sol_reserve,
            entry_impact_pct=round(entry_impact, 4),
            exit_impact_pct=round(exit_impact, 4),
            expected_roundtrip_impact_pct=round(expected_roundtrip, 4),
            stressed_roundtrip_impact_pct=round(stressed_roundtrip, 4),
            passes_depth_screen=passes_depth,
            max_safe_swap_usd=max_safe_usd,
            dynamic_position_size_usd=dyn_size_usd,
            is_dynamically_viable=is_viable,
        )
