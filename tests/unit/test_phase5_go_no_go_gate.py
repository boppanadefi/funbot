"""
Unit tests for Phase 5: Evaluation Metrics (The Go/No-Go Gate).
Validates the 6 mandatory criteria:
1. Net Expectancy (SOL) > 0.0 after all TX fees, MEV penalties, and Jito tips.
2. TP1 Win Rate >= 60% (Hits +9% gross target).
3. Slippage Variance: Simulated avg slippage < 2.0% one-way.
4. Runner Contribution > 5% of total net P&L.
5. Max Drawdown < $10.00 (Respecting daily halt).
6. Stale Feed Rejects < 5% of signals rejected for WS latency.
"""

from datetime import datetime, timezone, timedelta
import json
import pytest

from src.engine.gate_evaluator_28d import GateEvaluator28Day
from src.engine.paper_ledger import PaperLedger
from src.engine.staleness_breaker import DataStalenessCircuitBreaker


@pytest.fixture
def sample_passing_trades():
    """Generates a synthetic 28-day batch of 20 trades where all 6 criteria pass."""
    now = datetime(2026, 10, 17, 10, 0, 0, tzinfo=timezone.utc)
    trades = []
    # 15 winning trades (75% TP1 win rate >= 60%), 5 losing trades
    for i in range(20):
        entry_t = (now - timedelta(days=25 - i)).isoformat()
        if i < 15:
            # Winner: TP1 hit (+9%), runner generated extra profit
            trades.append({
                "position_id": f"pos_win_{i}",
                "pool_address": f"PoolWin_{i}",
                "token_mint": f"MintWin_{i}",
                "regime": "REGIME_A",
                "entry_ts": entry_t,
                "exit_ts": entry_t,
                "entry_price": 0.00100,
                "entry_price_sol": 0.00100,
                "position_size_sol": 0.096774,
                "tp1_fill_prices": json.dumps([0.00109, 0.00109, 0.00109]),
                "runner_exit_price": 0.00130,  # +30% runner
                "exit_price_sol": 0.00111,
                "sol_spent": 0.096774,
                "sol_received": 0.107000,
                "all_tx_fees_sol": 0.002,
                "all_jito_tips_sol": 0.0025,
                "venue_fees_sol": 0.002,
                "tips_sol": 0.0025,
                "priority_fees_sol": 0.0003,
                "net_pnl_sol": 0.005426,  # Positive net P&L
                "net_pnl_usd": 0.84,
                "net_return_pct": 5.6,
                "exit_reason": "RUNNER_STRUCTURE_EXIT",
                "ratchet_activated": 1,
                "duration_minutes": 8.5,
                "avg_slippage_pct": 0.85,  # < 2.0%
                "runner_pnl_sol": 0.001200,  # > 5% contribution
            })
        else:
            # Loser: Hard stop before TP1
            trades.append({
                "position_id": f"pos_loss_{i}",
                "pool_address": f"PoolLoss_{i}",
                "token_mint": f"MintLoss_{i}",
                "regime": "REGIME_A",
                "entry_ts": entry_t,
                "exit_ts": entry_t,
                "entry_price": 0.00100,
                "entry_price_sol": 0.00100,
                "position_size_sol": 0.096774,
                "tp1_fill_prices": "[]",
                "runner_exit_price": None,
                "exit_price_sol": 0.00090,
                "sol_spent": 0.096774,
                "sol_received": 0.087096,
                "all_tx_fees_sol": 0.002,
                "all_jito_tips_sol": 0.0025,
                "venue_fees_sol": 0.002,
                "tips_sol": 0.0025,
                "priority_fees_sol": 0.0003,
                "net_pnl_sol": -0.007742,
                "net_pnl_usd": -1.20,
                "net_return_pct": -8.0,
                "exit_reason": "HARD_STOP",
                "ratchet_activated": 0,
                "duration_minutes": 3.2,
                "avg_slippage_pct": 0.90,
                "runner_pnl_sol": 0.0,
            })
    return trades


class TestPhase5GoNoGoGate:
    """Step-by-step validation of Phase 5 Go/No-Go Gate Evaluator."""

    def test_overall_go_decision_when_all_criteria_met(self, sample_passing_trades):
        breaker = DataStalenessCircuitBreaker()
        # Simulate healthy feed: 100 decision checks, 2 stale rejections (2.0% < 5.0%)
        breaker._total_decision_checks = 100
        breaker._stale_rejections_count = 2

        evaluator = GateEvaluator28Day()
        report = evaluator.evaluate(trades=sample_passing_trades, staleness_breaker=breaker)

        assert report.is_overall_passed is True
        assert "GO — APPROVED FOR LIVE MAINNET DEPLOYMENT" in report.go_decision
        assert report.total_metrics == 6
        assert report.passed_metrics == 6
        assert report.failed_metrics == 0

        # Verify individual metric passes
        metric_dict = {m.metric_name: m for m in report.metrics}
        assert metric_dict["Net Expectancy (SOL)"].is_passed is True
        assert metric_dict["TP1 Win Rate"].is_passed is True
        assert metric_dict["Slippage Variance"].is_passed is True
        assert metric_dict["Runner Contribution"].is_passed is True
        assert metric_dict["Max Drawdown"].is_passed is True
        assert metric_dict["Stale Feed Rejects"].is_passed is True

    def test_negative_net_expectancy_fails_and_terminates(self, sample_passing_trades):
        # Invert trades to net negative
        for t in sample_passing_trades:
            t["net_pnl_sol"] = -0.010000
            t["net_pnl_usd"] = -1.55

        evaluator = GateEvaluator28Day()
        report = evaluator.evaluate(trades=sample_passing_trades)

        assert report.is_overall_passed is False
        assert "NO-GO: STRATEGY TERMINATION" in report.go_decision
        net_m = next(m for m in report.metrics if m.metric_name == "Net Expectancy (SOL)")
        assert not net_m.is_passed
        assert "Strategy has no mathematical edge. Terminate." in net_m.implication_if_failed

    def test_tp1_win_rate_below_60_fails(self, sample_passing_trades):
        # Alter trades so only 5 of 20 hit TP1 (25% < 60%)
        for i, t in enumerate(sample_passing_trades):
            if i >= 5:
                t["tp1_fill_prices"] = "[]"
                t["exit_reason"] = "HARD_STOP"
                t["ratchet_activated"] = 0

        evaluator = GateEvaluator28Day()
        report = evaluator.evaluate(trades=sample_passing_trades)

        assert report.is_overall_passed is False
        tp1_m = next(m for m in report.metrics if m.metric_name == "TP1 Win Rate")
        assert not tp1_m.is_passed
        assert "Friction buster failing" in tp1_m.implication_if_failed

    def test_slippage_variance_exceeding_2_pct_fails(self, sample_passing_trades):
        # Set simulated average slippage to 2.85% (> 2.0%)
        for t in sample_passing_trades:
            t["avg_slippage_pct"] = 2.85

        evaluator = GateEvaluator28Day()
        report = evaluator.evaluate(trades=sample_passing_trades)

        assert report.is_overall_passed is False
        slip_m = next(m for m in report.metrics if m.metric_name == "Slippage Variance")
        assert not slip_m.is_passed
        assert "AMM depth model is miscalibrated" in slip_m.implication_if_failed

    def test_runner_contribution_below_5_pct_fails(self, sample_passing_trades):
        # Set runner P&L to 0.0 or negative (0% contribution <= 5%)
        for t in sample_passing_trades:
            t["runner_pnl_sol"] = 0.0
            t["runner_exit_price"] = None

        evaluator = GateEvaluator28Day()
        report = evaluator.evaluate(trades=sample_passing_trades)

        assert report.is_overall_passed is False
        runner_m = next(m for m in report.metrics if m.metric_name == "Runner Contribution")
        assert not runner_m.is_passed
        assert "revert to 100% TP1" in runner_m.implication_if_failed

    def test_max_drawdown_breaching_10_dollars_fails(self, sample_passing_trades):
        # Inject consecutive losses causing $12.50 drawdown
        for i in range(6):
            sample_passing_trades[i]["net_pnl_usd"] = -2.50
            sample_passing_trades[i]["net_pnl_sol"] = -0.016

        evaluator = GateEvaluator28Day()
        report = evaluator.evaluate(trades=sample_passing_trades)

        assert report.is_overall_passed is False
        dd_m = next(m for m in report.metrics if m.metric_name == "Max Drawdown")
        assert not dd_m.is_passed
        assert "Position sizing or stop logic is failing" in dd_m.implication_if_failed

    def test_stale_feed_rejects_exceeding_5_pct_fails(self, sample_passing_trades):
        breaker = DataStalenessCircuitBreaker()
        # 10 stale rejections out of 100 checks = 10.0% (> 5.0%)
        breaker._total_decision_checks = 100
        breaker._stale_rejections_count = 10

        evaluator = GateEvaluator28Day()
        report = evaluator.evaluate(trades=sample_passing_trades, staleness_breaker=breaker)

        assert report.is_overall_passed is False
        stale_m = next(m for m in report.metrics if m.metric_name == "Stale Feed Rejects")
        assert not stale_m.is_passed
        assert "Upgrade RPC" in stale_m.implication_if_failed

    def test_filter_28_day_trades_window(self):
        evaluator = GateEvaluator28Day()
        t0 = datetime(2026, 10, 17, 10, 0, 0, tzinfo=timezone.utc)
        test_trades = [
            {"position_id": "p1", "entry_ts": (t0 - timedelta(days=5)).isoformat()},
            {"position_id": "p2", "entry_ts": (t0 - timedelta(days=20)).isoformat()},
            {"position_id": "p3", "entry_ts": (t0 - timedelta(days=45)).isoformat()},  # Outside 28d
        ]
        filtered, days = evaluator.filter_28_day_trades(test_trades, now=t0, window_days=28)
        assert len(filtered) == 2
        assert "p3" not in [t["position_id"] for t in filtered]
