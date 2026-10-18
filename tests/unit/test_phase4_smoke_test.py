"""
Unit tests for Phase 4: 24-Hour Infrastructure & Execution Smoke Test.
Validates:
1. Trade Accelerator Configuration & Concurrency Override (concurrency=3, VUW=1.05, demand=$50, size=$2.00, REGIME_TEST).
2. AMM Drift Monitor (< 0.1% drift tolerance ceiling & 60s periodic checks).
3. Stress Injection Protocol:
   - Test A: The Daily Loss Halt (-$10.00 ceiling halt).
   - Test B: The Liquidity Collapse (30s >5% slippage force-close).
   - Test C: Token-2022 Rejection (TransferFee extension rejection before sizing).
   - Test D: Time-Sliced TP1 Logic (3 tranches with escalating MEV penalties: +0.5%, +1.0%, +1.5%).
4. 24-Hour Gate Evaluator (6-metric scorecard).
"""

from datetime import datetime, timezone, timedelta
import pytest

from src.engine.amm_drift_monitor import AMMDriftMonitor
from src.engine.gate_evaluator_24h import GateEvaluator24Hour
from src.engine.live_paper_trader import LivePaperTradingEngine
from src.engine.models import PositionState
from src.engine.paper_ledger import PaperLedger
from src.engine.stress_injection import StressInjectionProtocol
from src.engine.trade_accelerator import TradeAcceleratorConfig
from src.engine.virtual_amm import VirtualAMMMirror
from src.live.circuit_breaker import DailyCircuitBreaker


@pytest.fixture
def clean_circuit_breaker(tmp_path):
    state_file = tmp_path / "cb_state.json"
    return DailyCircuitBreaker(state_file_path=str(state_file))


@pytest.fixture
def clean_ledger(tmp_path):
    ledger_db = tmp_path / "test_ledger.db"
    return PaperLedger(db_path=str(ledger_db))


class TestTradeAcceleratorConfiguration:
    """Step 1: Test Trade Accelerator Configuration and Concurrency Overrides."""

    def test_default_trade_accelerator_config(self):
        config = TradeAcceleratorConfig()
        assert not config.enabled
        assert config.concurrency_limit == 3
        assert config.relaxed_vuw_threshold == 1.05
        assert config.relaxed_baseline_demand_usd == 50.0
        assert config.relaxed_min_dynamic_size_usd == 2.00
        assert config.session_regime_tag == "REGIME_TEST"

    def test_live_paper_trader_concurrency_override(self, tmp_path, clean_circuit_breaker):
        state_file = tmp_path / "active_pos.json"
        accelerator = TradeAcceleratorConfig(enabled=True, concurrency_limit=3)
        engine = LivePaperTradingEngine(
            db_path=str(tmp_path / "test_engine.db"),
            circuit_breaker=clean_circuit_breaker,
            state_file=str(state_file),
            accelerator_config=accelerator,
        )

        assert engine.max_concurrent_positions == 3
        assert engine.has_capacity_for_new_trade() is True

        # Open 3 positions
        t0 = datetime(2026, 10, 17, 10, 0, 0, tzinfo=timezone.utc)
        for i in range(3):
            cand = {
                "pool_address": f"Pool_Acc_{i}",
                "token_mint": f"Mint_Acc_{i}",
                "minutes_post_graduation": 15.0,
                "current_sol_reserve": 60.0,
                "current_token_reserve": 150_000_000.0,
                "dex_name": "pumpswap",
                "breakout_confirmed": True,
            }
            quote = {
                "price_sol": 0.0004,
                "liquidity_usd": 18000.0,
                "total_txns_5m": 25,
                "buys_5m": 18,
                "sells_5m": 7,
                "price_change_5m": 6.5,
                "volume_5m_usd": 4000.0,
                "volume_1m_usd": 60.0,
                "current_volume_usd_min": 60.0,  # Passes relaxed $50/min
                "breakout_confirmed": True,
                "consolidation_confirmed": True,
                "vuw": 1.10,
                "unique_buyers_current_1m": 11,
                "unique_buyers_prior_1m": 10,
                "delta_unique_buyers_5blocks": 3,
                "breakout_taker_buy_pct": 83.33,
                "taker_buy_sol": 15.0,
                "taker_sell_sol": 3.0,
                "aggression_ratio": 5.0,
            }
            pos = engine.on_candidate_detected(cand, quote, current_time=t0 + timedelta(seconds=i))
            assert pos is not None
            assert pos.regime == "REGIME_TEST"

        assert len(engine.get_all_active_positions()) == 3
        assert engine.has_capacity_for_new_trade() is False

        # 4th position must be blocked due to concurrency limit = 3
        cand4 = {
            "pool_address": "Pool_Acc_4",
            "token_mint": "Mint_Acc_4",
            "minutes_post_graduation": 15.0,
            "current_sol_reserve": 60.0,
            "current_token_reserve": 150_000_000.0,
            "dex_name": "pumpswap",
            "breakout_confirmed": True,
        }
        quote4 = {
            "price_sol": 0.0004,
            "liquidity_usd": 18000.0,
            "total_txns_5m": 25,
            "buys_5m": 18,
            "sells_5m": 7,
            "price_change_5m": 6.5,
            "volume_5m_usd": 4000.0,
            "volume_1m_usd": 60.0,
            "current_volume_usd_min": 60.0,
            "breakout_confirmed": True,
            "consolidation_confirmed": True,
            "vuw": 1.10,
            "unique_buyers_current_1m": 11,
            "unique_buyers_prior_1m": 10,
            "delta_unique_buyers_5blocks": 3,
            "breakout_taker_buy_pct": 83.33,
            "taker_buy_sol": 15.0,
            "taker_sell_sol": 3.0,
            "aggression_ratio": 5.0,
        }
        pos4 = engine.on_candidate_detected(cand4, quote4, current_time=t0 + timedelta(seconds=10))
        assert pos4 is None  # Blocked by concurrency limit


class TestAMMDriftMonitor:
    """Step 2: AMM Drift Monitor & RPC Comparison."""

    def test_amm_sync_within_tolerance(self):
        mirror = VirtualAMMMirror()
        mirror.register_pool(
            pool_address="PoolDriftTest01",
            token_mint="MintDriftTest01",
            initial_sol_reserve=100.0,
            initial_token_reserve=200_000_000.0,
        )
        monitor = AMMDriftMonitor(virtual_amm=mirror)

        # RPC reserves are virtually identical (0.02% drift)
        report = monitor.check_pool_drift(
            pool_address="PoolDriftTest01",
            rpc_sol_reserve=100.02,
            rpc_token_reserve=200_010_000.0,
        )
        assert not report.is_critical_failure
        assert report.max_drift_pct < 0.10
        assert not monitor.has_critical_failure()

    def test_amm_sync_critical_failure_exceeds_threshold(self):
        mirror = VirtualAMMMirror()
        mirror.register_pool(
            pool_address="PoolDriftFail01",
            token_mint="MintDriftFail01",
            initial_sol_reserve=100.0,
            initial_token_reserve=200_000_000.0,
        )
        monitor = AMMDriftMonitor(virtual_amm=mirror)

        # RPC reserves diverge by 0.5% (> 0.1% tolerance)
        report = monitor.check_pool_drift(
            pool_address="PoolDriftFail01",
            rpc_sol_reserve=100.55,
            rpc_token_reserve=200_000_000.0,
        )
        assert report.is_critical_failure
        assert report.max_drift_pct > 0.10
        assert monitor.has_critical_failure()

    def test_is_check_due_interval(self):
        monitor = AMMDriftMonitor(check_interval_seconds=60.0)
        t0 = datetime(2026, 10, 17, 10, 0, 0, tzinfo=timezone.utc)
        assert monitor.is_check_due("PoolX", now=t0) is True

        monitor.check_pool_drift("PoolX", 50.0, 100_000_000.0, check_time=t0)
        assert monitor.is_check_due("PoolX", now=t0 + timedelta(seconds=30)) is False
        assert monitor.is_check_due("PoolX", now=t0 + timedelta(seconds=61)) is True


class TestStressInjectionProtocol:
    """Step 3: Edge-Case Stress Injection Protocol."""

    def test_stress_test_a_daily_loss_halt(self, clean_circuit_breaker, clean_ledger):
        protocol = StressInjectionProtocol(
            circuit_breaker=clean_circuit_breaker,
            ledger=clean_ledger,
            sol_price_usd=155.0,
        )
        result = protocol.run_test_a_daily_loss_halt()
        assert result.passed is True
        assert result.test_id == "TEST_A"
        assert result.details["is_halted"] is True
        assert result.details["total_injected_loss_usd"] == 10.0
        assert result.details["logged_state"] == "DAILY_LOSS_CEILING_HIT"

    def test_stress_test_b_liquidity_collapse(self, clean_ledger):
        protocol = StressInjectionProtocol(
            ledger=clean_ledger,
            sol_price_usd=155.0,
        )
        result = protocol.run_test_b_liquidity_collapse()
        assert result.passed is True
        assert result.test_id == "TEST_B"
        assert result.details["final_state"] == PositionState.CLOSED_LIQUIDITY_DRAIN.value
        assert "Liquidity Collapse" in result.details["exit_reason"]

    def test_stress_test_c_token_2022_rejection(self, clean_circuit_breaker):
        engine = LivePaperTradingEngine(
            circuit_breaker=clean_circuit_breaker,
            sol_price_usd=155.0,
        )
        protocol = StressInjectionProtocol(
            paper_engine=engine,
            circuit_breaker=clean_circuit_breaker,
        )
        result = protocol.run_test_c_token_2022_rejection()
        assert result.passed is True
        assert result.test_id == "TEST_C"
        assert result.details["hard_blocked_before_sizing"] is True
        assert "Token-2022" in result.details["reject_reason"]

    def test_stress_test_d_time_sliced_tp1(self, clean_ledger):
        protocol = StressInjectionProtocol(
            ledger=clean_ledger,
            sol_price_usd=155.0,
        )
        result = protocol.run_test_d_time_sliced_tp1()
        assert result.passed is True
        assert result.test_id == "TEST_D"
        assert result.details["slices_count"] == 3
        assert result.details["escalating_mev_penalties_pct"] == [0.50, 1.00, 1.50]
        assert result.details["escalation_valid"] is True
        assert result.details["spacings_valid"] is True


class TestGateEvaluator24Hour:
    """Step 4: The 24-Hour Gate (Pass/Fail Criteria) Scorecard."""

    def test_gate_evaluation_overall_pass(self, clean_circuit_breaker, clean_ledger):
        mirror = VirtualAMMMirror()
        mirror.register_pool(
            pool_address="PoolGate01",
            token_mint="MintGate01",
            initial_sol_reserve=80.0,
            initial_token_reserve=200_000_000.0,
        )
        drift_monitor = AMMDriftMonitor(virtual_amm=mirror)
        drift_monitor.check_pool_drift("PoolGate01", 80.01, 200_005_000.0)

        evaluator = GateEvaluator24Hour(
            ledger=clean_ledger,
            drift_monitor=drift_monitor,
            circuit_breaker=clean_circuit_breaker,
        )

        report = evaluator.evaluate_gate(force_pass_trade_count=True)
        assert report.total_metrics == 6
        assert report.passed_metrics == 6
        assert report.failed_metrics == 0
        assert report.is_overall_passed is True

    def test_gate_evaluation_fails_on_drift_breach(self, clean_circuit_breaker, clean_ledger):
        mirror = VirtualAMMMirror()
        mirror.register_pool(
            pool_address="PoolGateBad",
            token_mint="MintGateBad",
            initial_sol_reserve=50.0,
            initial_token_reserve=100_000_000.0,
        )
        drift_monitor = AMMDriftMonitor(virtual_amm=mirror)
        # 1.0% drift exceeds 0.1%
        drift_monitor.check_pool_drift("PoolGateBad", 50.50, 100_000_000.0)

        evaluator = GateEvaluator24Hour(
            ledger=clean_ledger,
            drift_monitor=drift_monitor,
            circuit_breaker=clean_circuit_breaker,
        )

        report = evaluator.evaluate_gate(force_pass_trade_count=True)
        assert report.is_overall_passed is False
        assert report.failed_metrics >= 1
        amm_metric = next(m for m in report.metrics if m.metric_name == "AMM Reserve Sync")
        assert not amm_metric.is_passed
