"""
Unit Tests for Phase 2: The Realistic Execution Simulator (The "Friction Engine").
Verifies:
1. Self-Impact Modeling: Exact constant-product AMM (x * y = k) token calculation and reserve mutation.
2. Adversarial MEV Sandwich Penalty:
   - Entries: Front-runner buys 0.5-1.5 SOL immediately before execution.
   - Time-Sliced TP1 Sells: Escalating MEV penalties (+0.5%, +1.0%, +1.5%).
3. Slot Latency Simulation: 400ms - 2400ms in-flight delay (1 to 6 slots) absorbing adverse market drift.
4. Dynamic Fee Deduction: Exact PumpSwap tier fee at execution MC + Solana base fee + priority fee + p75 Jito bundle tip.
5. PathASimulator integration with the Friction Engine.
"""

from datetime import datetime, timezone, timedelta
import pytest

from src.cost.fee_tiers import FeeTierLookup
from src.engine.execution_simulator import FrictionExecutionSimulator, ExecutionResult
from src.engine.models import PositionState, PaperPosition
from src.engine.paper_ledger import PaperLedger
from src.engine.simulator import PathASimulator
from src.engine.virtual_amm import VirtualAMMMirror, VirtualPool


class TestSelfImpactModeling:
    """Verifies that buy and sell swaps calculate exact constant-product tokens and mutate reserves."""

    def test_buy_self_impact_and_reserve_mutation(self):
        mirror = VirtualAMMMirror()
        pool = mirror.register_pool(
            pool_address="PoolSelfImpactBuy",
            token_mint="TokenSelfImpactBuy",
            initial_sol_reserve=84.0,
            initial_token_reserve=206_900_000.0,
        )
        initial_price = pool.marginal_price_sol
        initial_sol = pool.sol_reserve
        initial_tokens = pool.token_reserve

        simulator = FrictionExecutionSimulator(
            virtual_amm=mirror,
            sol_price_usd=155.0,
            enable_mev_sandwich=False,  # Isolate self-impact
            enable_slot_latency=False,
            deterministic_seed=42,
        )

        trigger_time = datetime.now(timezone.utc)
        buy_sol = 1.0  # Buy 1.0 SOL

        res = simulator.simulate_buy(
            pool_address="PoolSelfImpactBuy",
            token_mint="TokenSelfImpactBuy",
            sol_amount=buy_sol,
            trigger_time=trigger_time,
            reference_price_sol=initial_price,
        )

        # 1. Fill price must be higher than screen price before trade due to non-linear curve
        assert res.fill_price_sol > initial_price
        assert res.self_impact_pct > 0.0

        # 2. Local AMM pool reserves must be mutated to reflect our own swap!
        assert pool.sol_reserve > initial_sol
        assert pool.token_reserve < initial_tokens
        assert abs(pool.sol_reserve - (initial_sol + buy_sol)) < 1e-6
        assert pool.token_reserve == res.new_token_reserve

    def test_sell_self_impact_and_reserve_mutation(self):
        mirror = VirtualAMMMirror()
        pool = mirror.register_pool(
            pool_address="PoolSelfImpactSell",
            token_mint="TokenSelfImpactSell",
            initial_sol_reserve=85.0,
            initial_token_reserve=200_000_000.0,
        )
        initial_price = pool.marginal_price_sol

        simulator = FrictionExecutionSimulator(
            virtual_amm=mirror,
            sol_price_usd=155.0,
            enable_mev_sandwich=False,
            enable_slot_latency=False,
        )

        trigger_time = datetime.now(timezone.utc)
        tokens_to_sell = 2_000_000.0  # 1% of pool tokens

        res = simulator.simulate_exit(
            pool_address="PoolSelfImpactSell",
            token_mint="TokenSelfImpactSell",
            tokens_to_sell=tokens_to_sell,
            trigger_time=trigger_time,
            reference_price_sol=initial_price,
        )

        assert res.fill_price_sol < initial_price
        assert res.self_impact_pct > 0.0
        assert pool.token_reserve > 200_000_000.0
        assert pool.sol_reserve < 85.0


class TestAdversarialMEVSandwichPenalty:
    """Verifies entry front-runner penalty and escalating TP1 slice adverse slippage."""

    def test_entry_mev_sandwich_front_runner_elevates_curve(self):
        mirror = VirtualAMMMirror()
        pool = mirror.register_pool(
            pool_address="PoolSandwichEntry",
            token_mint="TokenSandwichEntry",
            initial_sol_reserve=84.0,
            initial_token_reserve=206_900_000.0,
        )
        initial_price = pool.marginal_price_sol

        simulator = FrictionExecutionSimulator(
            virtual_amm=mirror,
            sol_price_usd=155.0,
            enable_mev_sandwich=True,
            enable_slot_latency=False,
            deterministic_seed=123,
        )

        res = simulator.simulate_buy(
            pool_address="PoolSandwichEntry",
            token_mint="TokenSandwichEntry",
            sol_amount=1.0,
            trigger_time=datetime.now(timezone.utc),
            reference_price_sol=initial_price,
        )

        # Front-runner bought between 0.5 and 1.5 SOL immediately before our trade
        assert 0.50 <= res.mev_sandwich_frontrun_sol <= 1.50
        # Total SOL added to pool = frontrun_sol + our buy_sol (1.0 SOL)
        expected_min_sol = 84.0 + 0.50 + 1.0
        assert pool.sol_reserve >= expected_min_sol
        # Fill price must be significantly higher due to front-runner sandwich
        assert res.fill_price_sol > initial_price

    def test_time_sliced_tp1_escalating_mev_slippage(self):
        mirror = VirtualAMMMirror()
        pool = mirror.register_pool(
            pool_address="PoolTP1Escalation",
            token_mint="TokenTP1Escalation",
            initial_sol_reserve=85.0,
            initial_token_reserve=200_000_000.0,
        )

        simulator = FrictionExecutionSimulator(
            virtual_amm=mirror,
            sol_price_usd=155.0,
            enable_mev_sandwich=True,
            enable_slot_latency=False,
        )

        slice_tokens = 500_000.0
        now = datetime.now(timezone.utc)

        # Slice 1: +0.5% adverse slippage
        res_slice1 = simulator.simulate_tp1_slice(
            pool_address="PoolTP1Escalation",
            token_mint="TokenTP1Escalation",
            slice_tokens=slice_tokens,
            slice_index=0,
            trigger_time=now,
            reference_price_sol=pool.marginal_price_sol,
        )
        assert res_slice1.mev_adverse_slippage_pct == 0.50

        # Slice 2: +1.0% adverse slippage (searchers recognize the pattern)
        res_slice2 = simulator.simulate_tp1_slice(
            pool_address="PoolTP1Escalation",
            token_mint="TokenTP1Escalation",
            slice_tokens=slice_tokens,
            slice_index=1,
            trigger_time=now + timedelta(seconds=1),
            reference_price_sol=pool.marginal_price_sol,
        )
        assert res_slice2.mev_adverse_slippage_pct == 1.00

        # Slice 3: +1.5% adverse slippage
        res_slice3 = simulator.simulate_tp1_slice(
            pool_address="PoolTP1Escalation",
            token_mint="TokenTP1Escalation",
            slice_tokens=slice_tokens,
            slice_index=2,
            trigger_time=now + timedelta(seconds=2),
            reference_price_sol=pool.marginal_price_sol,
        )
        assert res_slice3.mev_adverse_slippage_pct == 1.50

        # Effective fill prices must degrade progressively across slices
        assert res_slice1.fill_price_sol > res_slice2.fill_price_sol > res_slice3.fill_price_sol


class TestSlotLatencySimulation:
    """Verifies block delay (400ms to 2400ms) and absorption of in-flight price moves."""

    def test_slot_latency_distribution(self):
        simulator = FrictionExecutionSimulator(
            enable_slot_latency=True,
            deterministic_seed=777,
        )
        latencies = [simulator.sample_slot_latency_ms()[0] for _ in range(50)]
        for lat in latencies:
            assert 400.0 <= lat <= 2400.0
            assert lat % 400.0 == 0.0  # Must be discrete Solana block times

    def test_in_flight_adverse_price_drift(self):
        mirror = VirtualAMMMirror()
        mirror.register_pool(
            pool_address="PoolDrift",
            token_mint="TokenDrift",
            initial_sol_reserve=84.0,
            initial_token_reserve=206_900_000.0,
        )

        simulator = FrictionExecutionSimulator(
            virtual_amm=mirror,
            sol_price_usd=155.0,
            enable_mev_sandwich=False,
            enable_slot_latency=True,
            deterministic_seed=42,
        )

        t0 = datetime(2026, 10, 17, 10, 0, 0, tzinfo=timezone.utc)
        # Price drops 10% while transaction is in flight
        res = simulator.simulate_buy(
            pool_address="PoolDrift",
            token_mint="TokenDrift",
            sol_amount=1.0,
            trigger_time=t0,
            reference_price_sol=84.0 / 206_900_000.0,
            in_flight_price_drift_pct=-10.0,
        )

        assert res.slot_latency_ms >= 400.0
        assert res.fill_time > t0
        # Fill time must match trigger + slot_latency_ms
        expected_fill = t0 + timedelta(milliseconds=res.slot_latency_ms)
        assert res.fill_time == expected_fill


class TestDynamicFeeDeduction:
    """Verifies tier fee, base fee, priority fee, and p75 Jito tip deductions."""

    def test_tier_fee_and_jito_p75_overhead(self):
        mirror = VirtualAMMMirror()
        mirror.register_pool(
            pool_address="PoolFees",
            token_mint="TokenFees",
            initial_sol_reserve=84.0,
            initial_token_reserve=206_900_000.0,
        )

        simulator = FrictionExecutionSimulator(
            virtual_amm=mirror,
            sol_price_usd=155.0,
            enable_mev_sandwich=False,
            enable_slot_latency=False,
        )

        res = simulator.simulate_buy(
            pool_address="PoolFees",
            token_mint="TokenFees",
            sol_amount=1.0,
            trigger_time=datetime.now(timezone.utc),
            reference_price_sol=84.0 / 206_900_000.0,
        )

        # Tier 1 fee for ~400 SOL MC is 1.250% = 0.0125 SOL
        assert res.fee_tier == 1
        assert abs(res.venue_fee_sol - 0.0125) < 1e-4

        # Fixed Solana base fee (5000 lamports = 0.000005 SOL)
        assert res.base_fee_sol == 0.000005
        # Priority fee
        assert res.priority_fee_sol == 0.00015
        # Simulated Jito tip: p75 of recent bundle tips = 0.0025 SOL
        assert res.jito_tip_sol == 0.0025

        expected_total = res.venue_fee_sol + 0.000005 + 0.00015 + 0.0025
        assert abs(res.total_friction_sol - expected_total) < 1e-6


class TestPathASimulatorFrictionEngineIntegration:
    """Verifies end-to-end integration of FrictionExecutionSimulator in PathASimulator."""

    def test_simulator_open_and_scaleout_with_friction_engine(self, tmp_path):
        ledger = PaperLedger(db_path=str(tmp_path / "ledger.db"))
        mirror = VirtualAMMMirror()
        pool = mirror.register_pool(
            pool_address="PoolFrictionSim",
            token_mint="TokenFrictionSim",
            initial_sol_reserve=84.0,
            initial_token_reserve=206_900_000.0,
        )

        exec_sim = FrictionExecutionSimulator(
            virtual_amm=mirror,
            sol_price_usd=155.0,
            enable_mev_sandwich=True,
            enable_slot_latency=True,
            deterministic_seed=101,
        )

        sim = PathASimulator(
            ledger=ledger,
            sol_price_usd=155.0,
            execution_simulator=exec_sim,
            enable_friction_engine=True,
        )

        t0 = datetime(2026, 10, 17, 10, 0, 0, tzinfo=timezone.utc)
        pos = sim.open_position(
            position_id="pos_friction_001",
            pool_address="PoolFrictionSim",
            token_mint="TokenFrictionSim",
            regime="REGIME_A",
            market_cap_sol=380.0,
            entry_price_sol=pool.marginal_price_sol,
            position_notional_usd=15.0,
            entry_time=t0,
        )

        # 1. Entry price was filled through execution simulator with self-impact & sandwich
        assert pos.entry_price_sol > (84.0 / 206_900_000.0)
        assert pos.entry_leg.jito_tip_sol == 0.0025  # p75 Jito tip
        assert pos.entry_leg.base_fee_sol == 0.000005
        assert pos.entry_time > t0  # Absorbed slot latency delay

        # 2. Trigger TP1 when price rises above TP1 target
        pos = sim.update_price_tick(
            pos=pos,
            current_price_sol=pos.tp1_price_sol * 1.01,
            tick_time=t0 + timedelta(minutes=5),
        )
        assert pos.state == PositionState.RUNNER_ACTIVE
        assert pos.tp1_activated is True
        assert len(pos.tp1_legs) == 3

        # Slices experienced escalating adverse MEV slippage
        assert pos.tp1_legs[0].mev_penalty_pct == 0.50
        assert pos.tp1_legs[1].mev_penalty_pct == 1.00
        assert pos.tp1_legs[2].mev_penalty_pct == 1.50
