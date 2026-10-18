"""
Phase 2: The Realistic Execution Simulator (The "Friction Engine").
Enforces brutal real-world Solana execution dynamics for Paper Trading:
1. Self-Impact Modeling: Constant-product AMM (x * y = k) token pricing and reserve mutation.
2. Adversarial MEV Sandwich Penalty:
   - Entries: Front-runner buys 0.5-1.5 SOL immediately before execution, elevating curve.
   - Time-Sliced TP1 Sells: Escalating MEV penalty (Slice 1: +0.5%, Slice 2: +1.0%, Slice 3: +1.5%).
3. Slot Latency Simulation: 400ms - 2400ms in-flight delay (1 to 6 Solana blocks) absorbing live price drift.
4. Dynamic Fee Deduction: Exact PumpSwap tier fee at execution MC + Solana base fee + priority fee + p75 Jito bundle tip.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
import logging
import random
from typing import Dict, List, Optional, Tuple

from src.cost.fee_tiers import FeeTierLookup, FeeTierResult
from src.cost.jito_tips import JitoTipModel
from src.engine.models import ExecutionLeg
from src.engine.virtual_amm import VirtualAMMMirror, VirtualPool

logger = logging.getLogger("ExecutionSimulator")


@dataclass
class ExecutionResult:
    """Exact output produced by the Realistic Execution Simulator."""
    leg_type: str
    trigger_time: datetime
    fill_time: datetime
    slot_latency_ms: float
    slots_elapsed: int
    fill_price_sol: float
    screen_price_before_sol: float
    sol_amount: float
    token_amount: float
    venue_fee_sol: float
    base_fee_sol: float
    priority_fee_sol: float
    jito_tip_sol: float
    total_friction_sol: float
    self_impact_pct: float
    mev_sandwich_frontrun_sol: float
    mev_adverse_slippage_pct: float
    simulated_market_cap_sol: float
    fee_tier: int
    new_sol_reserve: float
    new_token_reserve: float
    net_sol_amount: float = 0.0

    def to_execution_leg(self) -> ExecutionLeg:
        """Converts simulator result to standard ExecutionLeg domain model."""
        return ExecutionLeg(
            leg_type=self.leg_type,
            timestamp=self.fill_time,
            price_sol=self.fill_price_sol,
            sol_amount=self.sol_amount,
            token_amount=self.token_amount,
            venue_fee_sol=self.venue_fee_sol,
            jito_tip_sol=self.jito_tip_sol,
            priority_fee_sol=self.priority_fee_sol,
            base_fee_sol=self.base_fee_sol,
            self_impact_pct=self.self_impact_pct,
            slot_latency_ms=self.slot_latency_ms,
            mev_penalty_pct=self.mev_adverse_slippage_pct,
            frontrun_sol=self.mev_sandwich_frontrun_sol,
        )


class FrictionExecutionSimulator:
    """
    Simulates ruthless real-world transaction execution on Solana AMMs.
    Guarantees no trade fills at frictionless screen price.
    """

    SOLANA_BLOCK_TIME_MS: float = 400.0
    MIN_SLOT_LATENCY_MS: float = 400.0   # 1 slot
    MAX_SLOT_LATENCY_MS: float = 2400.0  # 6 slots
    SOLANA_BASE_FEE_SOL: float = 0.000005  # 5,000 lamports
    PRIORITY_FEE_SOL: float = 0.00015
    P75_JITO_BUNDLE_TIP_SOL: float = 0.0025  # p75 of recent bundle tips

    # Time-Sliced TP1 Escalating MEV Penalty
    TP1_MEV_PENALTY_SLICES = [0.005, 0.010, 0.015]  # +0.5%, +1.0%, +1.5%

    def __init__(
        self,
        virtual_amm: Optional[VirtualAMMMirror] = None,
        sol_price_usd: float = 155.0,
        enable_mev_sandwich: bool = True,
        enable_slot_latency: bool = True,
        enable_self_impact: bool = True,
        deterministic_seed: Optional[int] = None,
    ):
        self.virtual_amm = virtual_amm or VirtualAMMMirror()
        self.sol_price_usd = sol_price_usd
        self.fee_lookup = FeeTierLookup()
        self.tip_model = JitoTipModel(sol_price_usd)
        self.enable_mev_sandwich = enable_mev_sandwich
        self.enable_slot_latency = enable_slot_latency
        self.enable_self_impact = enable_self_impact
        self._rng = random.Random(deterministic_seed) if deterministic_seed is not None else random

    def sample_slot_latency_ms(self) -> Tuple[float, int]:
        """
        Samples transaction landing latency from real Solana block distribution:
        Between 400ms (1 slot) and 2400ms (6 slots).
        Returns: (latency_ms, slots_count)
        """
        if not self.enable_slot_latency:
            return 0.0, 0
        slots = self._rng.randint(1, 6)
        latency_ms = slots * self.SOLANA_BLOCK_TIME_MS
        return latency_ms, slots

    def sample_frontrun_sol(self) -> float:
        """
        Samples adversarial MEV front-runner buy size: 0.5 to 1.5 SOL.
        """
        if not self.enable_mev_sandwich:
            return 0.0
        return round(self._rng.uniform(0.50, 1.50), 4)

    def _ensure_pool(
        self,
        pool_address: str,
        token_mint: str,
        reference_price_sol: float,
        initial_sol_reserve: float = 84.0,
    ) -> VirtualPool:
        """Retrieves or auto-registers candidate pool in the local AMM mirror."""
        pool = self.virtual_amm.get_pool(pool_address)
        if not pool:
            initial_token_res = initial_sol_reserve / reference_price_sol if reference_price_sol > 0 else 206_900_000.0
            pool = self.virtual_amm.register_pool(
                pool_address=pool_address,
                token_mint=token_mint,
                initial_sol_reserve=initial_sol_reserve,
                initial_token_reserve=initial_token_res,
            )
        return pool

    def simulate_buy(
        self,
        pool_address: str,
        token_mint: str,
        sol_amount: float,
        trigger_time: datetime,
        reference_price_sol: float,
        in_flight_price_drift_pct: float = 0.0,
    ) -> ExecutionResult:
        """
        Brutal simulated Buy execution:
        1. Latency delay (400ms to 2400ms) with in-flight price drift.
        2. Adversarial MEV front-run (0.5 - 1.5 SOL buy pushes the AMM curve).
        3. Self-impact constant product execution (x * y = k) mutating pool reserves.
        4. Dynamic fee deduction at execution market cap + p75 Jito tip.
        """
        pool = self._ensure_pool(pool_address, token_mint, reference_price_sol)
        screen_p_before = pool.marginal_price_sol

        # 1. Slot Latency Simulation (400ms to 2400ms)
        latency_ms, slots = self.sample_slot_latency_ms()
        fill_time = trigger_time + timedelta(milliseconds=latency_ms)

        # In-flight drift: If market moved while transaction was in flight
        if in_flight_price_drift_pct != 0.0:
            drift_factor = 1.0 + (in_flight_price_drift_pct / 100.0)
            if drift_factor > 0:
                pool.sol_reserve *= drift_factor
                pool.k = pool.sol_reserve * pool.token_reserve

        # 2. Adversarial MEV Sandwich Penalty (Front-runner buys 0.5 - 1.5 SOL immediately before you)
        frontrun_sol = self.sample_frontrun_sol()
        if frontrun_sol > 0.0:
            # Front-runner executes first, pushing AMM curve up
            tier_front = self.fee_lookup.get_tier(pool.marginal_price_sol * 1_000_000_000.0)
            net_front_sol = frontrun_sol * (1.0 - (tier_front.total_fee_pct / 100.0))
            new_token_res_front = pool.k / (pool.sol_reserve + net_front_sol)
            tokens_front_out = max(1.0, pool.token_reserve - new_token_res_front)
            pool.apply_swap(is_buy=True, sol_amount=frontrun_sol, token_amount=tokens_front_out)
            logger.debug(
                "MEV Front-Runner bought %.3f SOL on %s. AMM price elevated: %.8f -> %.8f SOL",
                frontrun_sol, pool_address, screen_p_before, pool.marginal_price_sol
            )

        # 3. Dynamic Fee Deduction: Tier lookup at execution market cap
        mc_sol_pre = pool.marginal_price_sol * 1_000_000_000.0
        tier = self.fee_lookup.get_tier(mc_sol_pre)
        fee_rate = tier.total_fee_pct / 100.0
        venue_fee_sol = sol_amount * fee_rate

        # 4. Self-Impact Modeling (Exact tokens received using x * y = k before trade)
        net_sol_in = sol_amount - venue_fee_sol
        new_token_reserve = pool.k / (pool.sol_reserve + net_sol_in)
        tokens_received = max(0.0, pool.token_reserve - new_token_reserve)
        fill_price_sol = sol_amount / tokens_received if tokens_received > 0 else pool.marginal_price_sol
        self_impact_pct = max(0.0, ((fill_price_sol - screen_p_before) / screen_p_before) * 100.0) if screen_p_before > 0 else 0.0

        # Update local AMM reserves to reflect our own swap
        if self.enable_self_impact:
            pool.apply_swap(is_buy=True, sol_amount=sol_amount, token_amount=tokens_received)

        # Transaction overheads: Base fee + Priority fee + p75 Jito tip
        base_fee_sol = self.SOLANA_BASE_FEE_SOL
        priority_fee_sol = self.PRIORITY_FEE_SOL
        jito_tip_sol = self.P75_JITO_BUNDLE_TIP_SOL
        total_friction_sol = venue_fee_sol + base_fee_sol + priority_fee_sol + jito_tip_sol
        mc_exec = fill_price_sol * 1_000_000_000.0

        return ExecutionResult(
            leg_type="ENTRY",
            trigger_time=trigger_time,
            fill_time=fill_time,
            slot_latency_ms=latency_ms,
            slots_elapsed=slots,
            fill_price_sol=fill_price_sol,
            screen_price_before_sol=screen_p_before,
            sol_amount=sol_amount,
            token_amount=tokens_received,
            venue_fee_sol=venue_fee_sol,
            base_fee_sol=base_fee_sol,
            priority_fee_sol=priority_fee_sol,
            jito_tip_sol=jito_tip_sol,
            total_friction_sol=total_friction_sol,
            self_impact_pct=round(self_impact_pct, 4),
            mev_sandwich_frontrun_sol=frontrun_sol,
            mev_adverse_slippage_pct=0.0,
            simulated_market_cap_sol=mc_exec,
            fee_tier=tier.tier,
            new_sol_reserve=pool.sol_reserve,
            new_token_reserve=pool.token_reserve,
            net_sol_amount=net_sol_in,
        )

    def simulate_tp1_slice(
        self,
        pool_address: str,
        token_mint: str,
        slice_tokens: float,
        slice_index: int,
        trigger_time: datetime,
        reference_price_sol: float,
        in_flight_price_drift_pct: float = 0.0,
    ) -> ExecutionResult:
        """
        Simulates execution of a single 30% TP1 slice:
        Slice 1 (30%): +0.5% adverse slippage.
        Slice 2 (30%): +1.0% adverse slippage (searchers recognize pattern).
        Slice 3 (30%): +1.5% adverse slippage.
        """
        pool = self._ensure_pool(pool_address, token_mint, reference_price_sol)
        screen_p_before = pool.marginal_price_sol

        # Slot latency for this slice
        latency_ms, slots = self.sample_slot_latency_ms()
        fill_time = trigger_time + timedelta(milliseconds=latency_ms)

        # In-flight drift
        if in_flight_price_drift_pct != 0.0:
            drift_factor = 1.0 + (in_flight_price_drift_pct / 100.0)
            if drift_factor > 0:
                pool.sol_reserve *= drift_factor
                pool.k = pool.sol_reserve * pool.token_reserve

        # 1. Escalating MEV Penalty
        penalty_idx = min(slice_index, len(self.TP1_MEV_PENALTY_SLICES) - 1)
        mev_penalty_pct = self.TP1_MEV_PENALTY_SLICES[penalty_idx] if self.enable_mev_sandwich else 0.0

        # 2. Self-Impact: Constant Product token sell (x * y = k)
        new_token_res = pool.token_reserve + slice_tokens
        new_sol_res_gross = pool.k / new_token_res
        gross_sol_out = max(0.0, pool.sol_reserve - new_sol_res_gross)

        # Apply escalating adverse MEV slippage penalty to gross SOL proceeds
        penalized_gross_sol = gross_sol_out * (1.0 - mev_penalty_pct)

        # Dynamic Fee: tier lookup at execution market cap
        effective_p = penalized_gross_sol / slice_tokens if slice_tokens > 0 else pool.marginal_price_sol
        mc_exec = effective_p * 1_000_000_000.0
        tier = self.fee_lookup.get_tier(mc_exec)
        venue_fee_sol = penalized_gross_sol * (tier.total_fee_pct / 100.0)
        net_sol_received = penalized_gross_sol - venue_fee_sol

        # Self-impact on pool reserves
        if self.enable_self_impact:
            pool.apply_swap(is_buy=False, sol_amount=gross_sol_out, token_amount=slice_tokens)

        # Transaction overheads: Base fee + Priority fee + p75 Jito tip (per slice)
        base_fee_sol = self.SOLANA_BASE_FEE_SOL
        priority_fee_sol = self.PRIORITY_FEE_SOL
        jito_tip_sol = self.P75_JITO_BUNDLE_TIP_SOL / 3.0  # Shared across the 3 slices
        total_friction_sol = venue_fee_sol + base_fee_sol + priority_fee_sol + jito_tip_sol

        self_impact_pct = max(0.0, ((screen_p_before - effective_p) / screen_p_before) * 100.0) if screen_p_before > 0 else 0.0

        return ExecutionResult(
            leg_type=f"TP1_SLICE_{slice_index + 1}",
            trigger_time=trigger_time,
            fill_time=fill_time,
            slot_latency_ms=latency_ms,
            slots_elapsed=slots,
            fill_price_sol=effective_p,
            screen_price_before_sol=screen_p_before,
            sol_amount=penalized_gross_sol,
            token_amount=slice_tokens,
            venue_fee_sol=venue_fee_sol,
            base_fee_sol=base_fee_sol,
            priority_fee_sol=priority_fee_sol,
            jito_tip_sol=jito_tip_sol,
            total_friction_sol=total_friction_sol,
            self_impact_pct=round(self_impact_pct, 4),
            mev_sandwich_frontrun_sol=0.0,
            mev_adverse_slippage_pct=round(mev_penalty_pct * 100.0, 2),
            simulated_market_cap_sol=mc_exec,
            fee_tier=tier.tier,
            new_sol_reserve=pool.sol_reserve,
            new_token_reserve=pool.token_reserve,
            net_sol_amount=net_sol_received,
        )

    def simulate_exit(
        self,
        pool_address: str,
        token_mint: str,
        tokens_to_sell: float,
        trigger_time: datetime,
        reference_price_sol: float,
        leg_type: str = "EXIT",
        in_flight_price_drift_pct: float = 0.0,
    ) -> ExecutionResult:
        """
        Simulates full exit or runner exit with self-impact, slot latency, and p75 Jito overhead.
        """
        pool = self._ensure_pool(pool_address, token_mint, reference_price_sol)
        screen_p_before = pool.marginal_price_sol

        # Slot latency
        latency_ms, slots = self.sample_slot_latency_ms()
        fill_time = trigger_time + timedelta(milliseconds=latency_ms)

        if in_flight_price_drift_pct != 0.0:
            drift_factor = 1.0 + (in_flight_price_drift_pct / 100.0)
            if drift_factor > 0:
                pool.sol_reserve *= drift_factor
                pool.k = pool.sol_reserve * pool.token_reserve

        # Constant Product execution
        new_token_res = pool.token_reserve + tokens_to_sell
        new_sol_res_gross = pool.k / new_token_res
        gross_sol_out = max(0.0, pool.sol_reserve - new_sol_res_gross)

        effective_p = gross_sol_out / tokens_to_sell if tokens_to_sell > 0 else pool.marginal_price_sol
        mc_exec = effective_p * 1_000_000_000.0
        tier = self.fee_lookup.get_tier(mc_exec)
        venue_fee_sol = gross_sol_out * (tier.total_fee_pct / 100.0)
        net_sol_received = gross_sol_out - venue_fee_sol

        if self.enable_self_impact:
            pool.apply_swap(is_buy=False, sol_amount=gross_sol_out, token_amount=tokens_to_sell)

        base_fee_sol = self.SOLANA_BASE_FEE_SOL
        priority_fee_sol = self.PRIORITY_FEE_SOL
        jito_tip_sol = self.P75_JITO_BUNDLE_TIP_SOL
        total_friction_sol = venue_fee_sol + base_fee_sol + priority_fee_sol + jito_tip_sol

        self_impact_pct = max(0.0, ((screen_p_before - effective_p) / screen_p_before) * 100.0) if screen_p_before > 0 else 0.0

        return ExecutionResult(
            leg_type=leg_type,
            trigger_time=trigger_time,
            fill_time=fill_time,
            slot_latency_ms=latency_ms,
            slots_elapsed=slots,
            fill_price_sol=effective_p,
            screen_price_before_sol=screen_p_before,
            sol_amount=gross_sol_out,
            token_amount=tokens_to_sell,
            venue_fee_sol=venue_fee_sol,
            base_fee_sol=base_fee_sol,
            priority_fee_sol=priority_fee_sol,
            jito_tip_sol=jito_tip_sol,
            total_friction_sol=total_friction_sol,
            self_impact_pct=round(self_impact_pct, 4),
            mev_sandwich_frontrun_sol=0.0,
            mev_adverse_slippage_pct=0.0,
            simulated_market_cap_sol=mc_exec,
            fee_tier=tier.tier,
            new_sol_reserve=pool.sol_reserve,
            new_token_reserve=pool.token_reserve,
            net_sol_amount=net_sol_received,
        )
