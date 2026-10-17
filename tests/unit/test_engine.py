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
    # TP1 (The Friction Buster) executes: 90% of tokens liquidated!
    pos = sim.update_price_tick(pos, 0.00110, start_time + timedelta(minutes=4))
    assert pos.state == PositionState.RUNNER_ACTIVE
    assert pos.tp1_activated is True
    assert pos.tp1_leg is not None
    assert pos.tp1_leg.token_amount == pytest.approx(pos.tokens_held * 0.90)
    # Runner trailing stop is active: 15% below peak (0.00110 * 0.85 = 0.000935)
    assert pos.current_stop_price_sol == pytest.approx(0.00110 * 0.85)

    # 4. Tick 3: Free Runner surges to 0.00150 (+50%). Stop trails at 0.00150 * 0.85 = 0.001275.
    pos = sim.update_price_tick(pos, 0.00150, start_time + timedelta(minutes=6))
    assert pos.peak_price_sol == 0.00150
    assert pos.current_stop_price_sol == pytest.approx(0.00150 * 0.85)

    # 5. Tick 4: Price pulls back to 0.00125 (hits runner trailing stop 0.001275).
    # Runner exit triggered! Liquidates remaining 10% tokens.
    closed_pos = sim.update_price_tick(pos, 0.00125, start_time + timedelta(minutes=8))
    assert closed_pos.state == PositionState.CLOSED_TRAIL
    assert closed_pos.exit_reason == "TRAILING_STOP"
    assert closed_pos.runner_leg is not None
    assert closed_pos.runner_leg.token_amount == pytest.approx(closed_pos.tokens_held * 0.10)
    assert closed_pos.realized_net_pnl_usd > 0  # Solid net profit!

    # Check persistent ledger
    trades = temp_ledger.get_all_trades()
    assert len(trades) == 1
    assert trades[0]["position_id"] == "pos_scaleout_win"
    assert trades[0]["ratchet_activated"] == 1


def test_friction_busting_runner_dumps_to_zero(temp_ledger):
    """
    Verifies the crucial mathematical advantage of 90/10 Scale-Out:
    On a $20 trade, 90% ($18) sold at +9% gross locks in +$1.62.
    If the remaining $2 runner (10%) dumps completely to 0, gross loss is only -$0.38
    (vastly superior to the -$2.56 loss under 80/20 and -$3.50 loss under old 100% stop).
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

    # 1. Price hits +9% TP1 -> 90% sold to lock net profit after friction
    pos = sim.update_price_tick(pos, 0.00109, start_time + timedelta(minutes=2))
    assert pos.tp1_activated is True
    assert pos.state == PositionState.RUNNER_ACTIVE

    # 2. Token dumps to near zero (0.000001 SOL)
    closed = sim.update_price_tick(pos, 0.000001, start_time + timedelta(minutes=4))
    assert closed.state == PositionState.CLOSED_TRAIL

    # Net loss on trade is limited to ~$0.38 gross
    gross_sol_received = pos.tp1_leg.sol_amount + pos.runner_leg.sol_amount
    gross_pnl_usd = (gross_sol_received - pos.sol_spent) * sim.sol_price_usd
    assert pytest.approx(gross_pnl_usd, abs=0.15) == -0.38

    # Realized net loss with all 3 slices + entry + runner exit is strictly bounded
    assert -3.20 <= closed.realized_net_pnl_usd <= -1.20


def test_runner_hard_time_kill_when_under_30_pct(temp_ledger):
    """
    Fix 5: If the 10% runner does not achieve +30% gross within 3 minutes of TP1,
    market dump it immediately without letting it bleed sideways while LP evaporates.
    """
    sim = PathASimulator(ledger=temp_ledger, sol_price_usd=155.0)
    start_time = datetime(2026, 10, 17, 12, 0, 0, tzinfo=timezone.utc)

    pos = sim.open_position(
        position_id="pos_runner_time_kill",
        pool_address="PoolTimeKill01",
        token_mint="MintTimeKill01",
        regime="REGIME_A",
        market_cap_sol=390.0,
        entry_price_sol=0.00100,
        tp1_target_gross_pct=0.09,
        position_notional_usd=20.0,
        entry_time=start_time,
    )

    # Minute 1: Price triggers TP1 at 0.00110 (+10% gross). TP1 activates!
    pos = sim.update_price_tick(pos, 0.00110, start_time + timedelta(minutes=1))
    assert pos.tp1_activated is True
    assert pos.state == PositionState.RUNNER_ACTIVE
    assert pos.tp1_time == start_time + timedelta(minutes=1)

    # Minute 2 (1m after TP1): Price moves to 0.00115 (+15% gross, < +30%). Still active.
    pos = sim.update_price_tick(pos, 0.00115, start_time + timedelta(minutes=2))
    assert not pos.is_closed

    # Minute 3 (2m after TP1): Price moves to 0.00120 (+20% gross, < +30%). Still active.
    pos = sim.update_price_tick(pos, 0.00120, start_time + timedelta(minutes=3))
    assert not pos.is_closed

    # Minute 4.1 (3.1m after TP1): 3 minutes have passed since TP1!
    # Price is 0.00118 (+18% gross < +30% threshold).
    # Hard Time Kill triggers immediately!
    closed = sim.update_price_tick(pos, 0.00118, start_time + timedelta(minutes=4, seconds=6))
    assert closed.is_closed is True
    assert closed.state == PositionState.CLOSED_RUNNER_TIME_KILL
    assert "RUNNER_HARD_TIME_KILL" in closed.exit_reason
    assert closed.runner_leg is not None
    assert closed.runner_leg.token_amount == pytest.approx(pos.tokens_held * 0.10)


def test_runner_exceeding_30_pct_survives_time_kill(temp_ledger):
    """
    Fix 5: If the runner successfully breaks out >= +30% within 3 minutes of TP1,
    it is NOT killed by the time-kill rule; it is allowed to run and trail structure stops.
    """
    sim = PathASimulator(ledger=temp_ledger, sol_price_usd=155.0)
    start_time = datetime(2026, 10, 17, 13, 0, 0, tzinfo=timezone.utc)

    pos = sim.open_position(
        position_id="pos_runner_moonshot",
        pool_address="PoolMoon01",
        token_mint="MintMoon01",
        regime="REGIME_A",
        market_cap_sol=390.0,
        entry_price_sol=0.00100,
        tp1_target_gross_pct=0.09,
        position_notional_usd=20.0,
        entry_time=start_time,
    )

    # Minute 1: Price triggers TP1 at 0.00110. TP1 activates!
    pos = sim.update_price_tick(pos, 0.00110, start_time + timedelta(minutes=1))
    assert pos.tp1_activated is True

    # Minute 2.5: Runner surges to 0.00140 (+40% gross >= +30% hurdle!).
    pos = sim.update_price_tick(pos, 0.00140, start_time + timedelta(minutes=2, seconds=30))
    assert not pos.is_closed

    # Minute 4.5 (3.5m after TP1): Price remains strong at 0.00138 (+38% >= +30%).
    # Hard Time Kill does NOT trigger because price achieved and held >= +30%!
    pos = sim.update_price_tick(pos, 0.00138, start_time + timedelta(minutes=4, seconds=30))
    assert not pos.is_closed
    assert pos.state == PositionState.RUNNER_ACTIVE



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


def test_time_sliced_tp1_execution(temp_ledger):
    """
    Verifies Time-Sliced TP1 (Anti-MEV / Sandwich Defense):
    1. Splits 90% TP1 tranche into exactly 3 equal sells (30% each).
    2. Spaces slices 2-3 blocks (~800ms-1.2s) apart.
    3. Confines curve impact per slice and preserves average fill near +9% target.
    4. Unlocks 10% lottery runner after slice 3.
    """
    sim = PathASimulator(ledger=temp_ledger, sol_price_usd=155.0)
    start_time = datetime.now(timezone.utc)

    pos = sim.open_position(
        position_id="pos_sliced_test",
        pool_address="PoolSlicedTP1",
        token_mint="TokenSlicedTP1",
        regime="REGIME_A",
        market_cap_sol=380.0,
        entry_price_sol=0.00100,
        position_notional_usd=20.0,
        entry_time=start_time,
        tp1_target_gross_pct=0.09,
    )

    assert pos.tp1_activated is False
    assert pos.tp1_slices_executed == 0
    assert len(pos.tp1_legs) == 0

    # Trigger TP1 at +10% gross (0.00110)
    pos = sim.update_price_tick(pos, 0.00110, start_time + timedelta(minutes=3))

    assert pos.state == PositionState.RUNNER_ACTIVE
    assert pos.tp1_activated is True
    assert pos.tp1_slices_executed == 3
    assert len(pos.tp1_legs) == 3

    # Verify each slice sells exactly 30% of tokens
    expected_slice_tokens = pos.tokens_held * 0.30
    total_slice_tokens = 0.0
    for idx, slice_leg in enumerate(pos.tp1_legs):
        assert slice_leg.leg_type == f"TP1_SLICE_{idx + 1}"
        assert slice_leg.token_amount == pytest.approx(expected_slice_tokens)
        total_slice_tokens += slice_leg.token_amount
        if idx > 0:
            delta_sec = (slice_leg.timestamp - pos.tp1_legs[idx - 1].timestamp).total_seconds()
            assert delta_sec == pytest.approx(1.0)  # 2-3 blocks interval

    # Verify total across slices is 90%
    assert total_slice_tokens == pytest.approx(pos.tokens_held * 0.90)
    # Composite leg represents the aggregate 90%
    assert pos.tp1_leg.token_amount == pytest.approx(pos.tokens_held * 0.90)
    # VWAP is near target (above 0.00108)
    assert pos.tp1_leg.price_sol > 0.00108
    # 10% tokens retained as lottery runner
    assert pos.runner_tokens_held == pytest.approx(pos.tokens_held * 0.10)


