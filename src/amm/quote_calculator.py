"""
AMM Math & Sell Quote Simulation Engine for PumpSwap Pools.
Implements exact constant-product integer mathematics (k = x * y),
price impact modeling, basis-point slippage bounds, and Path A+ exit simulations.
"""

from dataclasses import dataclass
import math
from typing import Optional, Tuple

from src.protocol.pumpswap import BondingCurveState


@dataclass(frozen=True)
class BuyQuote:
    sol_in_lamports: int
    fee_lamports: int
    net_sol_effective: int
    expected_tokens_out: int
    spot_price_lamports: float
    execution_price_lamports: float
    price_impact_pct: float
    max_sol_cost_lamports: int
    slippage_bps: int


@dataclass(frozen=True)
class SellQuote:
    tokens_in: int
    gross_sol_lamports: int
    fee_lamports: int
    net_sol_lamports: int
    spot_price_lamports: float
    execution_price_lamports: float
    price_impact_pct: float
    min_sol_output_lamports: int
    slippage_bps: int


class AMMQuoteCalculator:
    """Deterministic Constant-Product Quote Calculator for PumpSwap."""

    FEE_BPS: int = 100  # 1.00% protocol fee

    @classmethod
    def calculate_spot_price_lamports(cls, virtual_sol: int, virtual_token: int) -> float:
        """Returns spot price in lamports per atomic token unit (x / y)."""
        if virtual_token <= 0:
            return 0.0
        return virtual_sol / virtual_token

    @classmethod
    def get_buy_quote(
        cls,
        virtual_sol: int,
        virtual_token: int,
        sol_in_lamports: int,
        slippage_bps: int = 200,
        real_token_reserves: Optional[int] = None,
    ) -> BuyQuote:
        """
        Calculates exact tokens received for a given SOL input.
        Formula: dy = (y * dx_eff) / (x + dx_eff) where dx_eff = dx * (1 - fee)
        """
        if virtual_sol <= 0 or virtual_token <= 0:
            raise ValueError(f"Reserves must be positive: sol={virtual_sol}, token={virtual_token}")
        if sol_in_lamports <= 0:
            raise ValueError(f"SOL input must be > 0: got {sol_in_lamports}")

        # 1. Deduct 1% protocol fee from SOL input
        fee = (sol_in_lamports * cls.FEE_BPS) // 10000
        eff_sol = sol_in_lamports - fee

        # 2. Constant product swap: dy = (y * eff_sol) // (x + eff_sol)
        numerator = virtual_token * eff_sol
        denominator = virtual_sol + eff_sol
        tokens_out = numerator // denominator

        # Bound by real token reserves if specified
        if real_token_reserves is not None and tokens_out > real_token_reserves:
            tokens_out = max(0, real_token_reserves)

        # 3. Prices and price impact
        spot_p = cls.calculate_spot_price_lamports(virtual_sol, virtual_token)
        exec_p = sol_in_lamports / tokens_out if tokens_out > 0 else spot_p
        price_impact = max(0.0, ((exec_p - spot_p) / spot_p) * 100.0) if spot_p > 0 else 0.0

        # 4. Max SOL cost with slippage tolerance
        max_cost = sol_in_lamports + math.ceil(sol_in_lamports * (slippage_bps / 10000.0))

        return BuyQuote(
            sol_in_lamports=sol_in_lamports,
            fee_lamports=fee,
            net_sol_effective=eff_sol,
            expected_tokens_out=tokens_out,
            spot_price_lamports=spot_p,
            execution_price_lamports=exec_p,
            price_impact_pct=price_impact,
            max_sol_cost_lamports=max_cost,
            slippage_bps=slippage_bps,
        )

    @classmethod
    def get_sell_quote(
        cls,
        virtual_sol: int,
        virtual_token: int,
        tokens_in: int,
        slippage_bps: int = 200,
        real_sol_reserves: Optional[int] = None,
    ) -> SellQuote:
        """
        Calculates exact SOL received for a given token input.
        Formula: dx = (x * dy) / (y + dy), net_sol = dx * (1 - fee)
        """
        if virtual_sol <= 0 or virtual_token <= 0:
            raise ValueError(f"Reserves must be positive: sol={virtual_sol}, token={virtual_token}")
        if tokens_in <= 0:
            raise ValueError(f"Tokens input must be > 0: got {tokens_in}")

        # 1. Constant product output: dx = (x * dy) // (y + dy)
        numerator = virtual_sol * tokens_in
        denominator = virtual_token + tokens_in
        gross_sol = numerator // denominator

        # Bound by real SOL reserves if specified
        if real_sol_reserves is not None and gross_sol > real_sol_reserves:
            gross_sol = max(0, real_sol_reserves)

        # 2. Deduct 1% protocol fee
        fee = (gross_sol * cls.FEE_BPS) // 10000
        net_sol = gross_sol - fee

        # 3. Prices and price impact
        spot_p = cls.calculate_spot_price_lamports(virtual_sol, virtual_token)
        exec_p = gross_sol / tokens_in if tokens_in > 0 else spot_p
        price_impact = max(0.0, ((spot_p - exec_p) / spot_p) * 100.0) if spot_p > 0 else 0.0

        # 4. Min SOL output with slippage tolerance
        min_output = max(0, net_sol - math.ceil(net_sol * (slippage_bps / 10000.0)))

        return SellQuote(
            tokens_in=tokens_in,
            gross_sol_lamports=gross_sol,
            fee_lamports=fee,
            net_sol_lamports=net_sol,
            spot_price_lamports=spot_p,
            execution_price_lamports=exec_p,
            price_impact_pct=price_impact,
            min_sol_output_lamports=min_output,
            slippage_bps=slippage_bps,
        )

    @classmethod
    def simulate_path_a_exit(
        cls,
        curve_state: BondingCurveState,
        token_balance: int,
        slippage_bps: int = 200,
        exit_reserve_lamports: int = 5_000_000,  # 0.005 SOL
    ) -> Tuple[bool, SellQuote, str]:
        """
        Simulates Path A+ single exit for 100% of token holdings.
        Enforces Invariant 2:
        - 100% position liquidation in a single swap.
        - Slippage <= 2.0% (200 bps).
        - Net SOL output positive and sufficient to preserve solvency.
        """
        if token_balance <= 0:
            return False, cls.get_sell_quote(1, 1, 1), "Zero token balance"

        quote = cls.get_sell_quote(
            virtual_sol=curve_state.virtual_sol_reserves,
            virtual_token=curve_state.virtual_token_reserves,
            tokens_in=token_balance,
            slippage_bps=slippage_bps,
            real_sol_reserves=curve_state.real_sol_reserves,
        )

        if quote.price_impact_pct > 2.0:
            return False, quote, f"Exit price impact excessive: {quote.price_impact_pct:.2f}% > 2.0%"

        if quote.net_sol_lamports <= 0:
            return False, quote, "Net SOL output is non-positive"

        return True, quote, "Path A+ exit criteria satisfied"

    @classmethod
    def check_dynamic_liquidity_floor(
        cls,
        curve_state: BondingCurveState,
        test_swap_lamports: int = 20_000_000,  # ~0.02 SOL (~$3.00 at $150/SOL)
        max_allowed_impact_pct: float = 1.5,
    ) -> bool:
        """
        Verifies dynamic liquidity floor (Check 9 of 12-Check gate):
        A trade of test_swap_lamports must encounter <= max_allowed_impact_pct price impact.
        """
        try:
            quote = cls.get_buy_quote(
                virtual_sol=curve_state.virtual_sol_reserves,
                virtual_token=curve_state.virtual_token_reserves,
                sol_in_lamports=test_swap_lamports,
            )
            return quote.price_impact_pct <= max_allowed_impact_pct
        except Exception:
            return False
