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


def test_friction_busting_scale_out_execution(temp_ledger):
    sim = PathASimulator(ledger=temp_ledger, sol_price_usd=155.0)
    start_time = datetime.now(timezone.utc)

    # 1. Open Position: Entry at 0.00100 SOL, $20 notional, TP1 target at +9.0% (0.00109 SOL)
    pos = sim.open_position(
        position_id="pos_scaleout_win",
        pool_address="PoolScaleOut",
        token_mint="TokenScaleOut",
        regime="REGIME_A",
        market_cap_sol=390.0,
        entry_price_sol=0.00100,
        tp1_target_gross_pct=0.09,
        position_notional_usd=20.0,
        trailing_pct=0.15,
        entry_time=start_time,
        trailing_mode="PERCENTAGE",
    )
    assert pos.state == PositionState.OPEN
    assert pos.tp1_activated is False
    assert pos.current_stop_price_sol == pytest.approx(0.00090)  # -10% initial protective stop
    assert pos.tp1_price_sol == pytest.approx(0.00109)          # +9% TP1 trigger

    # 2. Tick 1: Price rises to 0.00105 (+5%, below +9% TP1). State remains OPEN.
    pos = sim.update_price_tick(pos, 0.00105, start_time + timedelta(minutes=2))
    assert pos.state == PositionState.OPEN
    assert pos.tp1_activated is False

    # 3. Tick 2: Price hits 0.00110 (+10% gross, crosses +9% TP1 target!).
    # TP1 (The Friction Buster) executes: 80% of tokens liquidated!
    pos = sim.update_price_tick(pos, 0.00110, start_time + timedelta(minutes=4))
    assert pos.state == PositionState.RUNNER_ACTIVE
    assert pos.tp1_activated is True
    assert pos.tp1_leg is not None
    assert pos.tp1_leg.token_amount == pytest.approx(pos.tokens_held * 0.80)
    # Runner trailing stop is active: 15% below peak (0.00110 * 0.85 = 0.000935)
    assert pos.current_stop_price_sol == pytest.approx(0.00110 * 0.85)

    # 4. Tick 3: Free Runner surges to 0.00150 (+50%). Stop trails at 0.00150 * 0.85 = 0.001275.
    pos = sim.update_price_tick(pos, 0.00150, start_time + timedelta(minutes=6))
    assert pos.peak_price_sol == 0.00150
    assert pos.current_stop_price_sol == pytest.approx(0.00150 * 0.85)

    # 5. Tick 4: Price pulls back to 0.00125 (hits runner trailing stop 0.001275).
    # Runner exit triggered! Liquidates remaining 20% tokens.
    closed_pos = sim.update_price_tick(pos, 0.00125, start_time + timedelta(minutes=8))
    assert closed_pos.state == PositionState.CLOSED_TRAIL
    assert closed_pos.exit_reason == "TRAILING_STOP"
    assert closed_pos.runner_leg is not None
    assert closed_pos.runner_leg.token_amount == pytest.approx(closed_pos.tokens_held * 0.20)
    assert closed_pos.realized_net_pnl_usd > 0  # Solid net profit!

    # Check persistent ledger
    trades = temp_ledger.get_all_trades()
    assert len(trades) == 1
    assert trades[0]["position_id"] == "pos_scaleout_win"
    assert trades[0]["ratchet_activated"] == 1


def test_friction_busting_runner_dumps_to_zero(temp_ledger):
    """
    Verifies the crucial mathematical advantage:
    On a $20 trade, TP1 locks in +$1.44 (covering friction).
    If the remaining $4 runner dumps completely, net loss is only -$2.56
    instead of the -$3.50 loss incurred under the old 100% stop model.
    """
    sim = PathASimulator(ledger=temp_ledger, sol_price_usd=155.0)
    start_time = datetime.now(timezone.utc)

    pos = sim.open_position(
        position_id="pos_runner_dump",
        pool_address="PoolDump",
        token_mint="TokenDump",
        regime="REGIME_A",
        market_cap_sol=390.0,
        entry_price_sol=0.00100,
        tp1_target_gross_pct=0.09,
        position_notional_usd=20.0,
        entry_time=start_time,
    )

    # 1. Price hits +9% TP1 -> 80% sold to cover friction
    pos = sim.update_price_tick(pos, 0.00109, start_time + timedelta(minutes=2))
    assert pos.tp1_activated is True
    assert pos.state == PositionState.RUNNER_ACTIVE

    # 2. Token dumps to near zero (0.000001 SOL)
    closed = sim.update_price_tick(pos, 0.000001, start_time + timedelta(minutes=4))
    assert closed.state == PositionState.CLOSED_TRAIL

    # Net loss on trade is limited to ~$2.56 gross (vastly superior to the -$20 loss if held to 0)
    gross_sol_received = pos.tp1_leg.sol_amount + pos.runner_leg.sol_amount
    gross_pnl_usd = (gross_sol_received - pos.sol_spent) * sim.sol_price_usd
    assert pytest.approx(gross_pnl_usd, abs=0.15) == -2.56

    # Realized net loss with all 3 legs of fees/tips is strictly bounded and superior to older full-loss stops
    assert -3.60 <= closed.realized_net_pnl_usd <= -2.00



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

