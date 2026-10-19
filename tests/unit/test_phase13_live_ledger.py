"""
Unit and Integration Test Suite for Phase 13: Live Execution Walk-Forward & Performance Ledger.
Validates:
1. LiveLedgerManager SQLite WAL persistence, entry/exit logging, and aggregation metrics.
2. SlippageAttributionEngine price impact math, fee decomposition, and MEV sandwich detection.
3. WalkForwardValidator comparative divergence tracking between Paper and Live ledgers.
4. LiveScorecard 28-day rolling evaluation and Milestone 3 Go/No-Go gate.
"""

import json
import os
from pathlib import Path
import tempfile
import time
from unittest.mock import AsyncMock, MagicMock
import pytest

from src.engine.paper_ledger import PaperLedger
from src.live.friction_attribution import (
    SlippageAttributionEngine,
    FrictionAttributionReport,
)
from src.live.live_ledger import (
    LiveLedgerManager,
    LiveTradeRecord,
)
from src.live.live_scorecard import (
    LiveScorecard,
    LiveScorecardReport,
    ScorecardCriterion,
)
from src.live.walk_forward_validator import (
    WalkForwardValidator,
    DivergenceStatus,
)
from src.orchestrator.alerting import AlertDispatcher, AlertEvent, AlertSeverity


@pytest.fixture
def temp_dir():
    with tempfile.TemporaryDirectory() as td:
        yield Path(td)


@pytest.fixture
def test_live_ledger(temp_dir):
    db_path = temp_dir / "test_live_ledger.db"
    return LiveLedgerManager(db_path=str(db_path), sol_price_usd=155.0)


@pytest.fixture
def mock_alerts():
    dispatcher = MagicMock(spec=AlertDispatcher)
    dispatcher.dispatch = AsyncMock(return_value={"mock": True})
    return dispatcher


# ==============================================================================
# 1. LiveLedgerManager Tests
# ==============================================================================

def test_live_ledger_record_entry(test_live_ledger):
    """Verifies entry recording, slippage calculation, and SQLite WAL persistence."""
    rec = test_live_ledger.record_entry(
        position_id="live_pos_001",
        pool_address="PoolAddr1111111111111111111111111111111111",
        token_mint="TokenMint111111111111111111111111111111111",
        regime="REGIME_POST_GRAD",
        entry_signature="sig_entry_123",
        sol_spent=0.01935,
        size_tokens=42000,
        quoted_price_sol=0.000450,
        actual_fill_price_sol=0.0004545,  # +1.0% adverse slippage
        venue_fee_sol=0.000048,
        priority_fee_sol=0.00005,
        jito_tip_sol=0.00010,
    )

    assert rec.position_id == "live_pos_001"
    assert rec.entry_slippage_pct == pytest.approx(1.0, rel=1e-2)
    assert rec.status == "OPEN"

    # Verify retrieval
    loaded = test_live_ledger.get_trade("live_pos_001")
    assert loaded is not None
    assert loaded.size_tokens == 42000
    assert loaded.sol_spent == pytest.approx(0.01935, rel=1e-4)


def test_live_ledger_record_exit(test_live_ledger):
    """Verifies exit recording, realized PnL, total slippage, and closed status."""
    entry_ts = time.time() - 300.0  # 5 minutes ago
    test_live_ledger.record_entry(
        position_id="live_pos_002",
        pool_address="PoolAddr2222222222222222222222222222222222",
        token_mint="TokenMint222222222222222222222222222222222",
        regime="REGIME_POST_GRAD",
        entry_signature="sig_entry_456",
        sol_spent=0.020,
        size_tokens=40000,
        quoted_price_sol=0.000500,
        actual_fill_price_sol=0.0005025,  # +0.5% entry slippage
        entry_timestamp=entry_ts,
    )

    closed = test_live_ledger.record_exit(
        position_id="live_pos_002",
        exit_signature="sig_exit_789",
        sol_received=0.022,  # +10% gross gain
        quoted_price_sol=0.000550,
        actual_fill_price_sol=0.00054725,  # +0.5% exit slippage
        exit_reason="TP1_PATH_A_PLUS_EXIT",
        venue_fee_sol=0.00005,
        priority_fee_sol=0.00005,
        jito_tip_sol=0.00010,
        ratchet_activated=True,
    )

    assert closed is not None
    assert closed.status == "CLOSED"
    assert closed.exit_reason == "TP1_PATH_A_PLUS_EXIT"
    assert closed.duration_minutes >= 4.9
    assert closed.entry_slippage_pct == pytest.approx(0.5, rel=1e-2)
    assert closed.exit_slippage_pct == pytest.approx(0.5, rel=1e-2)
    assert closed.total_slippage_pct == pytest.approx(1.0, rel=1e-2)
    assert closed.net_pnl_sol > 0
    assert closed.net_pnl_usd > 0
    assert closed.ratchet_activated is True


def test_live_ledger_aggregates(test_live_ledger):
    """Verifies portfolio aggregate math over multiple closed trades."""
    # Winner
    test_live_ledger.record_entry("p1", "pool", "m1", "REGIME", "s1", 0.02, 1000, 0.00002, 0.00002)
    test_live_ledger.record_exit("p1", "s1_out", 0.023, 0.000023, 0.000023, "TP1")

    # Loser
    test_live_ledger.record_entry("p2", "pool", "m2", "REGIME", "s2", 0.02, 1000, 0.00002, 0.00002)
    test_live_ledger.record_exit("p2", "s2_out", 0.018, 0.000018, 0.000018, "STOP_LOSS")

    agg = test_live_ledger.get_aggregate_metrics()
    assert agg["total_trades"] == 2
    assert agg["winning_trades"] == 1
    assert agg["losing_trades"] == 1
    assert agg["win_rate_pct"] == 50.0
    assert agg["total_net_pnl_sol"] == pytest.approx(0.001, rel=1e-3)


# ==============================================================================
# 2. SlippageAttributionEngine Tests
# ==============================================================================

def test_slippage_calculation():
    """Verifies entry and exit slippage formulas."""
    engine = SlippageAttributionEngine()

    # Buy: paid 0.000525 vs quoted 0.000500 (+5.0% adverse slippage)
    entry_slip = engine.calculate_entry_slippage(quoted_price_sol=0.000500, actual_fill_price_sol=0.000525)
    assert entry_slip == pytest.approx(5.0, rel=1e-3)

    # Sell: received 0.000475 vs quoted 0.000500 (+5.0% adverse slippage)
    exit_slip = engine.calculate_exit_slippage(quoted_price_sol=0.000500, actual_fill_price_sol=0.000475)
    assert exit_slip == pytest.approx(5.0, rel=1e-3)


def test_theoretical_price_impact():
    """Verifies constant product AMM price impact formula: dx / (x + dx)."""
    engine = SlippageAttributionEngine()
    # 0.5 SOL trade on a 50 SOL reserve pool: 0.5 / 50.5 = ~0.99%
    impact = engine.calculate_theoretical_price_impact(trade_amount_sol=0.5, sol_reserve=50.0, token_reserve=1000000.0)
    assert impact == pytest.approx(0.9901, rel=1e-2)


def test_detect_mev_sandwich_clean():
    """Verifies normal execution within tolerance is not flagged as a sandwich."""
    engine = SlippageAttributionEngine()
    is_sandwich, conf, _ = engine.detect_mev_sandwich(
        entry_slippage_pct=0.85,
        expected_price_impact_pct=0.75,
    )
    assert is_sandwich is False
    assert conf == 0.0


def test_detect_mev_sandwich_spike_and_reversion():
    """Verifies sandwich detection when entry spikes high and post-block reverts downwards."""
    engine = SlippageAttributionEngine()
    is_sandwich, conf, reason = engine.detect_mev_sandwich(
        entry_slippage_pct=6.5,  # Huge entry spike
        expected_price_impact_pct=0.5,
        pre_swap_price_sol=0.000500,
        immediate_post_block_price_sol=0.000480,  # Reverted 4%
    )
    assert is_sandwich is True
    assert conf >= 0.90
    assert "sandwich" in reason.lower()


def test_attribute_trade_friction():
    """Verifies complete friction decomposition for a live trade."""
    engine = SlippageAttributionEngine()
    report = engine.attribute_trade_friction(
        position_id="test_decomp",
        token_mint="mint_decomp",
        quoted_entry_price_sol=0.00045,
        actual_entry_fill_price_sol=0.0004545,  # 1.0%
        quoted_exit_price_sol=0.000495,         # +10% gross alpha
        actual_exit_fill_price_sol=0.00049005,  # 1.0%
        sol_spent=0.02,
        actual_venue_fee_sol=0.0001,           # 0.5%
        actual_priority_fee_sol=0.00005,        # 0.25%
        actual_jito_tip_sol=0.0001,            # 0.5%
    )

    assert report.gross_alpha_pct == pytest.approx(10.0, rel=1e-2)
    assert report.total_slippage_pct == pytest.approx(2.0, rel=1e-2)
    assert report.total_friction_pct > 2.0
    assert report.net_realized_alpha_pct < report.gross_alpha_pct


# ==============================================================================
# 3. WalkForwardValidator Tests
# ==============================================================================

def test_walk_forward_validator_acceptable(test_live_ledger):
    """Verifies validator approves execution when live slippage is close to model (<2% delta)."""
    # Simulate a live trade with 0.85% total slippage
    test_live_ledger.record_entry("t1", "p", "m", "REG", "s", 0.02, 1000, 0.00002, 0.00002008)
    test_live_ledger.record_exit("t1", "s_out", 0.022, 0.000022, 0.00002191, "TP1")

    validator = WalkForwardValidator(live_ledger=test_live_ledger)
    status = validator.compute_divergence()

    assert status.is_acceptable is True
    assert status.slippage_variance_pct < 2.0
    assert len(status.warnings) == 0


def test_walk_forward_validator_divergence_warning(test_live_ledger):
    """Verifies validator flags divergence when live slippage exceeds threshold by >2%."""
    # Add 3 trades with heavy slippage (>3.5%)
    for i in range(3):
        pid = f"div_t_{i}"
        test_live_ledger.record_entry(pid, "p", "m", "REG", f"s_{i}", 0.02, 1000, 0.00002, 0.0000208)
        test_live_ledger.record_exit(pid, f"s_out_{i}", 0.019, 0.000021, 0.00002016, "STOP")

    validator = WalkForwardValidator(live_ledger=test_live_ledger, max_slippage_variance_pct=2.0)
    status = validator.compute_divergence()

    assert status.is_acceptable is False
    assert len(status.warnings) > 0
    assert any("slippage variance exceeded" in w.lower() for w in status.warnings)


# ==============================================================================
# 4. LiveScorecard 28-Day Performance Gate Tests
# ==============================================================================

def test_live_scorecard_empty(test_live_ledger):
    """Verifies safe empty report when no trades are recorded."""
    scorecard = LiveScorecard(live_ledger=test_live_ledger)
    report = scorecard.generate_report()

    assert report.total_trades == 0
    assert report.is_go_approved is False
    assert "NO COMPLETED LIVE TRADES" in report.verdict


def test_live_scorecard_go_decision(test_live_ledger):
    """Verifies Go decision when all 5 quantitative criteria pass."""
    # Record profitable trades with low slippage
    for i in range(5):
        pid = f"sc_win_{i}"
        test_live_ledger.record_entry(pid, "p", f"m_{i}", "REG", f"sig_{i}", 0.02, 1000, 0.00002, 0.00002008)
        test_live_ledger.record_exit(pid, f"sig_exit_{i}", 0.0225, 0.0000225, 0.00002241, "TP1")

    scorecard = LiveScorecard(live_ledger=test_live_ledger, bankroll_usd=25.0)
    report = scorecard.generate_report()

    assert report.total_trades == 5
    assert report.win_rate_pct == 100.0
    assert report.net_pnl_sol > 0
    assert report.max_drawdown_usd == 0.0
    assert report.is_go_approved is True
    assert "GO — APPROVED FOR FULL AUTONOMOUS SCALE" in report.verdict
