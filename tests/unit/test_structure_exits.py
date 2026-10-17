"""
Unit tests for Fix 3: Local-Structure & Time-Decay Exits.
1. Structure-Based Trailing Stop: Stop Loss = Lowest Low of previous two completed 1m candles.
   (Absorbs 30-40% single-block wicks without clipping while macro 1m trend holds higher lows).
2. Momentum Time-Decay Exit: If 2 consecutive 1m candles pass without making a new local high AND
   volume drops > 40% from entry-breakout volume, exit 100% immediately.
3. Liquidity Collapse Exit: Every 30 seconds, simulate sell quote. If estimated slippage on remaining
   position exceeds 5.0%, market dump immediately (LP is draining).
"""

from datetime import datetime, timezone, timedelta
import pytest
from src.engine.models import PositionState, PaperPosition
from src.engine.simulator import PathASimulator
from src.engine.structure_exits import (
    LocalStructureExitEngine,
    Candle1M,
)
from src.engine.paper_ledger import PaperLedger


@pytest.fixture
def temp_ledger(tmp_path):
    db_file = tmp_path / "test_struct_ledger.db"
    return PaperLedger(db_path=str(db_file))


def test_structure_trailing_stop_absorbs_wicks_and_ratchets(temp_ledger):
    """
    Verifies that the structure-based trailing stop:
    - Sets stop to Lowest Low of previous 2 completed 1m candles.
    - Absorbs single-block intra-candle wicks that would have clipped a 15% peak trail.
    - Ratchets upward as higher lows form, and triggers exit when structure breaks.
    """
    sim = PathASimulator(ledger=temp_ledger, sol_price_usd=155.0)
    start_time = datetime(2026, 10, 17, 10, 0, 0, tzinfo=timezone.utc)

    # 1. Open Position at 0.00100 SOL, TP1 at +9% (0.00109 SOL)
    pos = sim.open_position(
        position_id="pos_struct_test",
        pool_address="PoolStruct01",
        token_mint="MintStruct01",
        regime="REGIME_A",
        market_cap_sol=390.0,
        entry_price_sol=0.00100,
        tp1_target_gross_pct=0.09,
        position_notional_usd=20.0,
        entry_time=start_time,
        entry_breakout_volume_usd=2000.0,
    )
    assert pos.state == PositionState.OPEN

    # Complete Candle 1 (Minute 0: 10:00 - 10:01)
    # Open: 0.00100, High: 0.00115 (triggers TP1!), Low: 0.00096, Close: 0.00112, Vol: $2200
    c1 = Candle1M(
        minute_timestamp=start_time,
        open_sol=0.00100,
        high_sol=0.00115,
        low_sol=0.00096,
        close_sol=0.00112,
        volume_usd=2200.0,
    )
    pos = sim.update_price_tick(pos, 0.00112, start_time + timedelta(minutes=1), completed_candle=c1)
    assert pos.tp1_activated is True
    assert pos.state == PositionState.RUNNER_ACTIVE

    # Complete Candle 2 (Minute 1: 10:01 - 10:02)
    # Open: 0.00112, High: 0.00130, Low: 0.00105, Close: 0.00125, Vol: $2500
    c2 = Candle1M(
        minute_timestamp=start_time + timedelta(minutes=1),
        open_sol=0.00112,
        high_sol=0.00130,
        low_sol=0.00105,
        close_sol=0.00125,
        volume_usd=2500.0,
    )
    pos = sim.update_price_tick(pos, 0.00125, start_time + timedelta(minutes=2), completed_candle=c2)

    # Now we have 2 completed candles:
    # C1 Low: 0.00096, C2 Low: 0.00105
    # Lowest Low of previous 2 completed candles = min(0.00096, 0.00105) = 0.00096 SOL
    assert pos.structure_stop_price_sol == pytest.approx(0.00096)
    assert pos.lowest_low_2m_candles_sol == pytest.approx(0.00096)

    # Complete Candle 3 (Minute 2: 10:02 - 10:03) - forms higher low!
    # Open: 0.00125, High: 0.00140, Low: 0.00115, Close: 0.00135, Vol: $2800
    c3 = Candle1M(
        minute_timestamp=start_time + timedelta(minutes=2),
        open_sol=0.00125,
        high_sol=0.00140,
        low_sol=0.00115,
        close_sol=0.00135,
        volume_usd=2800.0,
    )
    pos = sim.update_price_tick(pos, 0.00135, start_time + timedelta(minutes=3), completed_candle=c3)

    # Previous two completed candles are now C2 (Low: 0.00105) and C3 (Low: 0.00115)
    # New Lowest Low = min(0.00105, 0.00115) = 0.00105 SOL!
    assert pos.structure_stop_price_sol == pytest.approx(0.00105)
    assert pos.current_stop_price_sol == pytest.approx(0.00105)

    # Wick Test during Minute 3:
    # A single-block cascade drops price to 0.00108 (a -22.8% drop from peak 0.00140).
    # Under a continuous 15% peak-trail stop (0.00140 * 0.85 = 0.00119), this would have STOPPED OUT at the bottom!
    # But with structure-based stop (0.00105), 0.00108 > 0.00105, so the wick is ABSORBED!
    pos = sim.update_price_tick(pos, 0.00108, start_time + timedelta(minutes=3, seconds=15))
    assert pos.state == PositionState.RUNNER_ACTIVE
    assert not pos.is_closed

    # Price then rebounds back up to 0.00130!
    pos = sim.update_price_tick(pos, 0.00130, start_time + timedelta(minutes=3, seconds=45))
    assert pos.state == PositionState.RUNNER_ACTIVE

    # Complete Candle 4 (Minute 3: 10:03 - 10:04) with low 0.00108
    c4 = Candle1M(
        minute_timestamp=start_time + timedelta(minutes=3),
        open_sol=0.00135,
        high_sol=0.00135,
        low_sol=0.00108,
        close_sol=0.00120,
        volume_usd=1800.0,
    )
    pos = sim.update_price_tick(pos, 0.00120, start_time + timedelta(minutes=4), completed_candle=c4)
    # Previous two candles C3 (low 0.00115) & C4 (low 0.00108) -> Stop = min(0.00115, 0.00108) = 0.00108 SOL
    assert pos.structure_stop_price_sol == pytest.approx(0.00108)

    # Finally, a macro structural break occurs: price drops to 0.00105 <= 0.00108
    closed = sim.update_price_tick(pos, 0.00105, start_time + timedelta(minutes=4, seconds=20))
    assert closed.is_closed
    assert closed.state == PositionState.CLOSED_TRAIL
    assert "Structure Trailing Stop" in closed.exit_reason


def test_momentum_time_decay_exit(temp_ledger):
    """
    Verifies that if 2 consecutive 1m candles pass without making a new local high,
    AND volume drops > 40% from entry-breakout volume, exit 100% immediately.
    """
    sim = PathASimulator(ledger=temp_ledger, sol_price_usd=155.0)
    start_time = datetime(2026, 10, 17, 11, 0, 0, tzinfo=timezone.utc)

    # Entry at 0.00100 with entry-breakout volume $2,000
    pos = sim.open_position(
        position_id="pos_decay_test",
        pool_address="PoolDecay01",
        token_mint="MintDecay01",
        regime="REGIME_A",
        market_cap_sol=390.0,
        entry_price_sol=0.00100,
        entry_time=start_time,
        entry_breakout_volume_usd=2000.0,
    )

    # Candle 1 (Minute 0): High 0.00110 (new local high = 0.00110), volume $2,100
    c1 = Candle1M(
        minute_timestamp=start_time,
        open_sol=0.00100,
        high_sol=0.00110,
        low_sol=0.00098,
        close_sol=0.00106,
        volume_usd=2100.0,
    )
    pos = sim.update_price_tick(pos, 0.00106, start_time + timedelta(minutes=1), completed_candle=c1)
    assert not pos.is_closed

    # Candle 2 (Minute 1): High 0.00108 (<= 0.00110: 1st stagnant candle), volume $1,500
    c2 = Candle1M(
        minute_timestamp=start_time + timedelta(minutes=1),
        open_sol=0.00106,
        high_sol=0.00108,
        low_sol=0.00102,
        close_sol=0.00105,
        volume_usd=1500.0,
    )
    pos = sim.update_price_tick(pos, 0.00105, start_time + timedelta(minutes=2), completed_candle=c2)
    assert not pos.is_closed

    # Candle 3 (Minute 2): High 0.00106 (<= 0.00110: 2nd consecutive stagnant candle!)
    # Volume drops to $1,000 (drop from $2,000 entry volume is 50%, which is > 40% threshold!)
    c3 = Candle1M(
        minute_timestamp=start_time + timedelta(minutes=2),
        open_sol=0.00105,
        high_sol=0.00106,
        low_sol=0.00103,
        close_sol=0.00104,
        volume_usd=1000.0,
    )
    closed = sim.update_price_tick(pos, 0.00104, start_time + timedelta(minutes=3), completed_candle=c3)

    assert closed.is_closed
    assert closed.state == PositionState.CLOSED_TIME_DECAY
    assert "Momentum Time-Decay Exit" in closed.exit_reason


def test_liquidity_collapse_exit(temp_ledger):
    """
    Verifies that every 30 seconds, if estimated slippage on remaining position exceeds 5%,
    market dump immediately without waiting for a price stop.
    """
    sim = PathASimulator(ledger=temp_ledger, sol_price_usd=155.0)
    start_time = datetime(2026, 10, 17, 12, 0, 0, tzinfo=timezone.utc)

    pos = sim.open_position(
        position_id="pos_liq_test",
        pool_address="PoolLiq01",
        token_mint="MintLiq01",
        regime="REGIME_A",
        market_cap_sol=390.0,
        entry_price_sol=0.00100,
        position_notional_usd=15.0,
        entry_time=start_time,
        entry_breakout_volume_usd=1500.0,
    )

    # Tick 1 at 15s: Slippage 2.0% (< 5%) -> Position remains open
    pos = sim.update_price_tick(
        pos,
        0.00102,
        start_time + timedelta(seconds=15),
        quote={"sell_slippage_pct": 2.0},
    )
    assert not pos.is_closed

    # Tick 2 at 35s: >= 30 seconds elapsed since entry.
    # Simulated sell slippage jumps to 6.5% (> 5.0% threshold) due to LP drain!
    closed = sim.update_price_tick(
        pos,
        0.00102,
        start_time + timedelta(seconds=35),
        quote={"sell_slippage_pct": 6.5},
    )

    assert closed.is_closed
    assert closed.state == PositionState.CLOSED_LIQUIDITY_DRAIN
    assert "Liquidity Collapse Exit" in closed.exit_reason
    assert "6.50% exceeds 5.0%" in closed.exit_reason
