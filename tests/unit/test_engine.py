"""
Unit Tests for Phase 4: Paper Trading Pipeline & Out-of-Sample Falsification.
Validates Guardrail 2 (Path A+ 100% Dynamic Trailing Single Exit) and Guardrail 7 (Risk Capacity & 20m Timeout).
"""

from datetime import datetime, timezone, timedelta
import pytest
from src.engine.models import PositionState, PaperPosition, ExecutionLeg
from src.engine.paper_ledger import PaperLedger
from src.engine.simulator import PathASimulator
from src.engine.risk_monitor import DailyRiskMonitor
from src.engine.validator import OutOfSampleValidator
from src.engine.hit_rate_surface import HitRateSurfaceAnalyzer


@pytest.fixture
def temp_ledger(tmp_path):
    db_file = tmp_path / "test_paper_ledger.db"
    return PaperLedger(db_path=str(db_file))


def test_path_a_ratchet_and_trailing_stop(temp_ledger):
    sim = PathASimulator(ledger=temp_ledger, sol_price_usd=155.0)
    start_time = datetime.now(timezone.utc)

    # 1. Open Position: Entry at 0.00100 SOL, Ratchet Trigger at +18.0% (0.00118 SOL)
    pos = sim.open_position(
        position_id="pos_win_1",
        pool_address="PoolPathA",
        token_mint="TokenPathA",
        regime="REGIME_A",
        market_cap_sol=390.0,
        entry_price_sol=0.00100,
        ratchet_trigger_gross_pct=18.0,
        position_notional_usd=15.0,
        trailing_pct=0.12,
        entry_time=start_time,
    )
    assert pos.state == PositionState.OPEN
    assert pos.ratchet_activated is False
    assert pos.current_stop_price_sol == pytest.approx(0.00090)  # -10% initial stop

    # 2. Tick 1: Price rises to 0.00115 (below hurdle). Stop unchanged.
    pos = sim.update_price_tick(pos, 0.00115, start_time + timedelta(minutes=2))
    assert pos.state == PositionState.OPEN
    assert pos.ratchet_activated is False

    # 3. Tick 2: Price surges to 0.00125 (crosses +18% hurdle!). Ratchet activated!
    pos = sim.update_price_tick(pos, 0.00125, start_time + timedelta(minutes=4))
    assert pos.state == PositionState.RATCHETED
    assert pos.ratchet_activated is True
    # Stop is ratcheted up into profit! (at least floor price)
    assert pos.current_stop_price_sol >= pos.ratchet_floor_price_sol

    # 4. Tick 3: Price hits new peak at 0.00150. Trailing stop trails peak by 12% (0.00150 * 0.88 = 0.00132)
    pos = sim.update_price_tick(pos, 0.00150, start_time + timedelta(minutes=6))
    assert pos.peak_price_sol == 0.00150
    assert pos.current_stop_price_sol == pytest.approx(0.00150 * 0.88)

    # 5. Tick 4: Price pulls back to 0.00130 (hits trailing stop 0.00132).
    # Single full exit triggered! Exactly 2 swaps total.
    closed_pos = sim.update_price_tick(pos, 0.00130, start_time + timedelta(minutes=8))
    assert closed_pos.state == PositionState.CLOSED_TRAIL
    assert closed_pos.exit_reason == "TRAILING_STOP"
    assert closed_pos.realized_net_pnl_usd > 0  # Profitable trade!

    # Check ledger persistence
    trades = temp_ledger.get_all_trades()
    assert len(trades) == 1
    assert trades[0]["position_id"] == "pos_win_1"
    assert trades[0]["ratchet_activated"] == 1


def test_path_a_hard_stop_loss(temp_ledger):
    sim = PathASimulator(ledger=temp_ledger, sol_price_usd=155.0)
    start_time = datetime.now(timezone.utc)

    pos = sim.open_position(
        position_id="pos_loss_1",
        pool_address="PoolLoss",
        token_mint="TokenLoss",
        regime="REGIME_A",
        market_cap_sol=390.0,
        entry_price_sol=0.00100,
        ratchet_trigger_gross_pct=18.0,
        position_notional_usd=15.0,
        entry_time=start_time,
    )

    # Price drops to 0.00088 (breaches initial protective stop 0.00090)
    closed = sim.update_price_tick(pos, 0.00088, start_time + timedelta(minutes=1))
    assert closed.state == PositionState.CLOSED_STOP
    assert closed.exit_reason == "HARD_STOP"
    assert closed.realized_net_pnl_usd < 0


def test_fallback_20m_timeout(temp_ledger):
    sim = PathASimulator(ledger=temp_ledger, sol_price_usd=155.0)
    start_time = datetime.now(timezone.utc)

    pos = sim.open_position(
        position_id="pos_stagnant",
        pool_address="PoolStagnant",
        token_mint="TokenStagnant",
        regime="REGIME_A",
        market_cap_sol=390.0,
        entry_price_sol=0.00100,
        ratchet_trigger_gross_pct=18.0,
        position_notional_usd=15.0,
        entry_time=start_time,
    )

    # Price hovers at 0.00102 for 21 minutes (Guardrail 7 timeout triggers)
    closed = sim.update_price_tick(pos, 0.00102, start_time + timedelta(minutes=21))
    assert closed.state == PositionState.CLOSED_TIMEOUT
    assert closed.exit_reason == "20M_TIMEOUT"


def test_daily_risk_monitor_concurrency_and_ceiling():
    monitor = DailyRiskMonitor(daily_loss_ceiling_usd=10.00)

    # 1. Normal opening permitted
    assert monitor.can_open_position(15.0, stress_loss_estimate_usd=1.50) is True

    # Register active position -> concurrency hits limit (max 1 position)
    pos = PaperPosition(
        position_id="p1", pool_address="a", token_mint="b", regime="REGIME_A",
        entry_time=datetime.now(timezone.utc), entry_price_sol=1.0, tokens_held=1.0,
        sol_spent=0.1, position_notional_usd=15.0, ratchet_trigger_price_sol=1.2,
        ratchet_floor_price_sol=1.1,
    )
    monitor.register_open_position(pos)
    assert monitor.can_open_position(15.0, stress_loss_estimate_usd=1.50) is False

    # Close with -$9.00 loss
    pos.realized_net_pnl_usd = -9.00
    monitor.register_closed_position(pos)
    assert monitor.remaining_daily_risk_budget_usd == 1.00

    # Next trade requires $1.50 stress loss, but remaining capacity is only $1.00 -> Rejected!
    assert monitor.can_open_position(15.0, stress_loss_estimate_usd=1.50) is False


def test_hit_rate_surface_and_validator():
    # Test break-even formula p* = |L| / (W + |L|)
    # Win = $3.00, Loss = $1.50 -> p* = 1.5 / 4.5 = 33.33%
    p_star = HitRateSurfaceAnalyzer.calculate_required_win_rate(3.00, -1.50)
    assert p_star == 33.33

    # Out of Sample Validator
    validator = OutOfSampleValidator()
    mock_trades = [
        {"position_id": f"t_{i}", "entry_ts": f"2026-10-1{i}T12:00:00", "net_pnl_usd": 2.50 if i % 4 != 0 else -1.20}
        for i in range(12)
    ]

    in_sample, out_of_sample = validator.split_chronological_dataset(mock_trades, in_sample_ratio=0.60)
    assert len(in_sample) == 7
    assert len(out_of_sample) == 5

    report = validator.evaluate_dataset(out_of_sample, "OUT_OF_SAMPLE")
    assert report.total_trades == 5
    assert report.net_pnl_usd > 0
    assert report.is_gate_passed is True


def test_live_paper_trading_engine(tmp_path):
    from src.engine.live_paper_trader import LivePaperTradingEngine
    from src.live.circuit_breaker import DailyCircuitBreaker
    from src.live.kill_switch import EmergencyKillSwitch

    db_file = tmp_path / "live_paper.db"
    state_file = tmp_path / "active_pos.json"
    cb_file = tmp_path / "cb.json"
    ks_file = tmp_path / "ks.json"

    cb = DailyCircuitBreaker(state_file_path=str(cb_file))
    ks = EmergencyKillSwitch(state_file_path=str(ks_file))

    engine = LivePaperTradingEngine(
        db_path=str(db_file),
        state_file=str(state_file),
        sol_price_usd=155.0,
        position_notional_usd=15.0,
        circuit_breaker=cb,
        kill_switch=ks,
    )
    assert engine.get_active_position() is None

    mock_pools = [
        {
            "pool_address": "PoolLive001",
            "token_mint": "TokenLive001",
            "state": "ACTIVE_WINDOW",
            "market_cap_sol": 380.0,
            "current_sol_reserve": 85.0,
            "current_token_reserve": 206_900_000.0,
        }
    ]

    # Tick 1: Automatically opens candidate
    pos_data = engine.on_market_tick(mock_pools)
    assert pos_data is not None
    assert pos_data["is_active"] is True
    assert pos_data["token_mint"] == "TokenLive001"

    # Verify active position getter
    active = engine.get_active_position()
    assert active is not None
    assert active["position_id"] == pos_data["position_id"]

    # Tick 2: Manual Close
    closed = engine.manual_close_active_position(reason="TEST_CLOSE")
    assert closed is not None
    assert closed["status"] == "MANUAL_CLOSED"
    assert engine.get_active_position() is None

