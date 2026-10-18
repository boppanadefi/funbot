"""
Unit tests for the 4 Candidate Selection Gates:
1. Pool Age: Retain 8–25 minutes initially; test alternative windows.
2. Liquidity & Reserve Consistency: Retain >= 25 SOL and >= $3,000, verify reserve consistency.
3. Activity: >= 3 transactions, minimum buy-side activity (buys >= 2) and buy-side dominance.
4. Momentum: Positive short-term price trend (5m >= 0%) with sustained buy pressure (5m vol >= $300).
"""

import pytest
from pathlib import Path
import tempfile
import shutil

from src.engine.live_paper_trader import LivePaperTradingEngine
from src.live.circuit_breaker import DailyCircuitBreaker
from src.live.kill_switch import EmergencyKillSwitch


@pytest.fixture
def paper_engine(tmp_path):
    db_file = tmp_path / "test_gates.db"
    state_file = tmp_path / "active_pos.json"
    cb_file = tmp_path / "cb.json"
    ks_file = tmp_path / "ks.json"

    cb = DailyCircuitBreaker(state_file_path=str(cb_file))
    ks = EmergencyKillSwitch(state_file_path=str(ks_file))

    return LivePaperTradingEngine(
        db_path=str(db_file),
        state_file=str(state_file),
        sol_price_usd=155.0,
        position_notional_usd=15.0,
        min_age_minutes=3.0,
        max_age_minutes=25.0,
        circuit_breaker=cb,
        kill_switch=ks,
    )


def test_gate_1_pool_age_default_window(paper_engine):
    pool_young = {"token_mint": "Mint1", "minutes_post_graduation": 2.0, "current_sol_reserve": 35.0}
    passed, reason = paper_engine.evaluate_candidate_gates(pool_young, quote=None)
    assert not passed
    assert "Pool Age Gate" in reason or "Minimum Cooldown Gate" in reason

    pool_valid = {"token_mint": "Mint2", "minutes_post_graduation": 15.0, "current_sol_reserve": 35.0}
    passed, reason = paper_engine.evaluate_candidate_gates(pool_valid, quote=None)
    assert passed

    pool_old = {"token_mint": "Mint3", "minutes_post_graduation": 30.0, "current_sol_reserve": 35.0}
    passed, reason = paper_engine.evaluate_candidate_gates(pool_old, quote=None)
    assert not passed
    assert "Pool Age Gate" in reason


def test_gate_1_alternative_window(tmp_path):
    # Test alternative window e.g. 5.0 to 18.0 minutes
    alt_engine = LivePaperTradingEngine(
        db_path=str(tmp_path / "alt.db"),
        state_file=str(tmp_path / "alt_state.json"),
        min_age_minutes=5.0,
        max_age_minutes=18.0,
    )
    pool_6m = {"token_mint": "MintAlt", "minutes_post_graduation": 6.0, "current_sol_reserve": 30.0}
    passed, _ = alt_engine.evaluate_candidate_gates(pool_6m, quote=None)
    assert passed

    pool_22m = {"token_mint": "MintAlt2", "minutes_post_graduation": 22.0, "current_sol_reserve": 30.0}
    passed, reason = alt_engine.evaluate_candidate_gates(pool_22m, quote=None)
    assert not passed
    assert "Pool Age Gate" in reason


def test_gate_2_liquidity_and_reserve_consistency(paper_engine):
    pool = {"token_mint": "MintLiq", "minutes_post_graduation": 12.0, "current_sol_reserve": 20.0}
    # On-chain reserve < 25 SOL
    passed, reason = paper_engine.evaluate_candidate_gates(pool, quote=None)
    assert not passed
    assert "Liquidity Gate" in reason

    # On-chain reserve 30 SOL, but DexScreener liquidity < $3,000
    pool["current_sol_reserve"] = 30.0
    quote_low_liq = {
        "price_sol": 0.0001,
        "liquidity_usd": 2500.0,
        "quote_sol_reserve": 15.0,
        "total_txns_5m": 5,
        "buys_5m": 3,
        "sells_5m": 2,
        "price_change_5m": 2.5,
        "volume_5m_usd": 500.0,
    }
    passed, reason = paper_engine.evaluate_candidate_gates(pool, quote=quote_low_liq)
    assert not passed
    assert "Liquidity Gate" in reason

    # Live liquidity $4,000, but implied total SOL (4000/155 = 25.8 SOL), however quote-side reserve drained to 5 SOL
    quote_drained_quote = {
        "price_sol": 0.0001,
        "liquidity_usd": 4000.0,
        "quote_sol_reserve": 5.0,  # < 12.0 SOL floor
        "total_txns_5m": 5,
        "buys_5m": 3,
        "sells_5m": 2,
        "price_change_5m": 2.5,
        "volume_5m_usd": 500.0,
    }
    passed, reason = paper_engine.evaluate_candidate_gates(pool, quote=quote_drained_quote)
    assert not passed
    assert "Reserve Consistency" in reason


def test_gate_3_activity_and_buy_dominance(paper_engine):
    pool = {"token_mint": "MintAct", "minutes_post_graduation": 12.0, "current_sol_reserve": 35.0}

    # Case A: Less than 3 transactions in 5 minutes
    quote_low_txns = {
        "price_sol": 0.0001,
        "liquidity_usd": 5000.0,
        "quote_sol_reserve": 16.0,
        "total_txns_5m": 2,
        "buys_5m": 2,
        "sells_5m": 0,
        "price_change_5m": 1.0,
        "volume_5m_usd": 400.0,
    }
    passed, reason = paper_engine.evaluate_candidate_gates(pool, quote=quote_low_txns)
    assert not passed
    assert "Activity Gate" in reason

    # Case B: Only 1 buy (insufficient buy-side activity)
    quote_1_buy = {
        "price_sol": 0.0001,
        "liquidity_usd": 5000.0,
        "quote_sol_reserve": 16.0,
        "total_txns_5m": 3,
        "buys_5m": 1,
        "sells_5m": 2,
        "price_change_5m": 1.0,
        "volume_5m_usd": 400.0,
    }
    passed, reason = paper_engine.evaluate_candidate_gates(pool, quote=quote_1_buy)
    assert not passed
    assert "Insufficient buy-side" in reason

    # Case C: Sell pressure dominates (e.g. 2 buys, 5 sells)
    quote_sell_dump = {
        "price_sol": 0.0001,
        "liquidity_usd": 5000.0,
        "quote_sol_reserve": 16.0,
        "total_txns_5m": 7,
        "buys_5m": 2,
        "sells_5m": 5,
        "price_change_5m": -2.0,
        "volume_5m_usd": 800.0,
    }
    passed, reason = paper_engine.evaluate_candidate_gates(pool, quote=quote_sell_dump)
    assert not passed
    assert "Sell pressure dominates" in reason


def test_gate_4_momentum_positive_trend_and_volume(paper_engine):
    pool = {"token_mint": "MintMom", "minutes_post_graduation": 12.0, "current_sol_reserve": 35.0}

    # Case A: Negative 5m price trend
    quote_neg_trend = {
        "price_sol": 0.0001,
        "liquidity_usd": 6000.0,
        "quote_sol_reserve": 18.0,
        "total_txns_5m": 6,
        "buys_5m": 3,
        "sells_5m": 3,
        "price_change_5m": -1.5,
        "volume_5m_usd": 500.0,
    }
    passed, reason = paper_engine.evaluate_candidate_gates(pool, quote=quote_neg_trend)
    assert not passed
    assert "Negative short-term trend" in reason

    # Case B: Stagnant volume (< $300 in 5m)
    quote_stagnant_vol = {
        "price_sol": 0.0001,
        "liquidity_usd": 6000.0,
        "quote_sol_reserve": 18.0,
        "total_txns_5m": 4,
        "buys_5m": 3,
        "sells_5m": 1,
        "price_change_5m": 0.5,
        "volume_5m_usd": 150.0,  # Stagnant
    }
    passed, reason = paper_engine.evaluate_candidate_gates(pool, quote=quote_stagnant_vol)
    assert not passed
    assert "Stagnant 5m volume" in reason

    # Case C: All candidate gates pass
    quote_valid = {
        "price_sol": 0.0001,
        "liquidity_usd": 6000.0,
        "quote_sol_reserve": 18.0,
        "total_txns_5m": 6,
        "buys_5m": 4,
        "sells_5m": 2,
        "price_change_5m": 3.2,
        "volume_5m_usd": 750.0,
        "breakout_confirmed": True,
    }
    passed, reason = paper_engine.evaluate_candidate_gates(pool, quote=quote_valid)
    assert passed
    assert "Passed All Candidate Gates" in reason


def test_gate_5_volume_exhaustion_consolidation_rejects_unexhausted_dump(paper_engine):
    pool = {"token_mint": "MintConsol", "minutes_post_graduation": 10.0, "current_sol_reserve": 35.0}
    # Peak spike vol was $2000. 1m vol is $600 (30% of peak >= 20% exhaustion threshold)
    quote_unexhausted = {
        "price_sol": 0.0001,
        "liquidity_usd": 6000.0,
        "quote_sol_reserve": 18.0,
        "total_txns_5m": 6,
        "buys_5m": 4,
        "sells_5m": 2,
        "price_change_5m": 3.2,
        "volume_5m_usd": 3000.0,
        "volume_1m_usd": 600.0,
        "spike_peak_1m_vol": 2000.0,
        "initial_spike_resistance_price": 0.00009,
    }
    passed, reason = paper_engine.evaluate_candidate_gates(pool, quote=quote_unexhausted)
    assert not passed
    assert "Volume Consolidation Gate" in reason


def test_gate_5_structural_breakout_rejects_below_resistance(paper_engine):
    pool = {"token_mint": "MintResist", "minutes_post_graduation": 10.0, "current_sol_reserve": 35.0}
    # 1m vol is $200 (< 20% of $2000 peak -> consolidated), but price 0.000095 < resistance 0.000100
    quote_below_resistance = {
        "price_sol": 0.000095,
        "liquidity_usd": 6000.0,
        "quote_sol_reserve": 18.0,
        "total_txns_5m": 6,
        "buys_5m": 4,
        "sells_5m": 2,
        "price_change_5m": 3.2,
        "volume_5m_usd": 1000.0,
        "volume_1m_usd": 200.0,
        "spike_peak_1m_vol": 2000.0,
        "initial_spike_resistance_price": 0.000100,
        "consolidation_avg_buy_vol": 50.0,
    }
    passed, reason = paper_engine.evaluate_candidate_gates(pool, quote=quote_below_resistance)
    assert not passed
    assert "Structural Breakout Gate" in reason


def test_gate_5_structural_breakout_rejects_low_buy_volume(paper_engine):
    pool = {"token_mint": "MintLowBuy", "minutes_post_graduation": 10.0, "current_sol_reserve": 35.0}
    # Consolidated, price broke above resistance, but buy volume is $60 <= 1.5x avg buy vol ($50 * 1.5 = $75)
    quote_low_buy_vol = {
        "price_sol": 0.000105,
        "liquidity_usd": 6000.0,
        "quote_sol_reserve": 18.0,
        "total_txns_5m": 6,
        "buys_5m": 4,
        "sells_5m": 2,
        "price_change_5m": 3.2,
        "volume_5m_usd": 1000.0,
        "volume_1m_usd": 200.0,
        "buy_volume_1m_usd": 60.0,
        "spike_peak_1m_vol": 2000.0,
        "initial_spike_resistance_price": 0.000100,
        "consolidation_avg_buy_vol": 50.0,
    }
    passed, reason = paper_engine.evaluate_candidate_gates(pool, quote=quote_low_buy_vol)
    assert not passed
    assert "Breakout Buy Volume Gate" in reason


def test_gate_5_breakout_confirmed_success(paper_engine):
    pool = {"token_mint": "MintSuccess", "minutes_post_graduation": 10.0, "current_sol_reserve": 35.0}
    quote_success = {
        "price_sol": 0.000105,
        "liquidity_usd": 6000.0,
        "quote_sol_reserve": 18.0,
        "total_txns_5m": 6,
        "buys_5m": 5,
        "sells_5m": 1,
        "price_change_5m": 3.2,
        "volume_5m_usd": 1000.0,
        "volume_1m_usd": 200.0,
        "buy_volume_1m_usd": 120.0,
        "spike_peak_1m_vol": 2000.0,
        "initial_spike_resistance_price": 0.000100,
        "consolidation_avg_buy_vol": 50.0,  # 120 > 1.5 * 50 = 75
    }
    passed, reason = paper_engine.evaluate_candidate_gates(pool, quote=quote_success)
    assert passed
    assert "Passed All Candidate Gates" in reason


def test_enforce_no_repeat_token_trading(tmp_path):
    """Verifies that once a token is traded in a session, it cannot be bought again."""
    db_file = tmp_path / "repeat_test.db"
    state_file = tmp_path / "repeat_state.json"
    cb_file = tmp_path / "cb.json"
    ks_file = tmp_path / "ks.json"

    cb = DailyCircuitBreaker(state_file_path=str(cb_file))
    ks = EmergencyKillSwitch(state_file_path=str(ks_file))

    engine = LivePaperTradingEngine(
        db_path=str(db_file),
        state_file=str(state_file),
        circuit_breaker=cb,
        kill_switch=ks,
    )

    mock_pool = {
        "pool_address": "PoolRepeat01",
        "token_mint": "MintRepeat01",
        "state": "ACTIVE_WINDOW",
        "minutes_post_graduation": 12.0,
        "current_sol_reserve": 85.0,
        "current_token_reserve": 206_900_000.0,
        "market_cap_sol": 380.0,
    }

    # Tick 1: Opens position on MintRepeat01
    pos1 = engine.on_market_tick([mock_pool])
    assert pos1 is not None
    assert pos1["token_mint"] == "MintRepeat01"
    assert "MintRepeat01" in engine.traded_tokens

    # Close position manually
    engine.manual_close_active_position(reason="TEST_CLOSE")
    assert engine.get_active_position() is None

    # Tick 2: Same pool comes again — must be rejected and not re-entered!
    pos2 = engine.on_market_tick([mock_pool])
    assert pos2 is None
    assert engine.get_active_position() is None


def test_gate_5_dead_token_rejected_below_baseline_demand_floor(paper_engine):
    """
    Fix 3 (Baseline Demand Floor):
    When volume drops to < 20% of spike peak, but falls below the $100/min baseline demand floor
    (e.g., $10/min), retail has abandoned the token.
    An isolated whale sweeping a few dollars of micro-liquidity must be HARD REJECTED.
    """
    pool = {"token_mint": "MintDeadToken", "minutes_post_graduation": 10.0, "current_sol_reserve": 35.0}

    # Case A: Volume drops to $15/min (< 20% of $2,000 peak = $400, but < $100 floor).
    # Isolated whale sweeps $30 of liquidity above resistance.
    quote_dead_whale_pump = {
        "price_sol": 0.000110,                    # Spiked above resistance
        "liquidity_usd": 6000.0,
        "quote_sol_reserve": 18.0,
        "total_txns_5m": 6,
        "buys_5m": 5,
        "sells_5m": 1,
        "price_change_5m": 8.0,
        "volume_5m_usd": 400.0,                   # 5m volume passes Gate 4 (>= $300)
        "volume_1m_usd": 15.0,                    # 1m volume = $15/min < $100 floor!
        "buy_volume_1m_usd": 30.0,
        "spike_peak_1m_vol": 2000.0,
        "initial_spike_resistance_price": 0.000100,
        "consolidation_avg_buy_vol": 10.0,
        "breakout_confirmed": True,               # Even if flagged, dead token floor MUST override!
    }

    passed, reason = paper_engine.evaluate_candidate_gates(pool, quote=quote_dead_whale_pump)
    assert not passed
    assert "Baseline Demand Floor Gate" in reason
    assert "dead token" in reason.lower()

    # Case B: Token drops to $10/min
    quote_dead_10usd = {
        "price_sol": 0.000105,
        "liquidity_usd": 6000.0,
        "quote_sol_reserve": 18.0,
        "total_txns_5m": 6,
        "buys_5m": 5,
        "sells_5m": 1,
        "price_change_5m": 3.2,
        "volume_5m_usd": 350.0,                   # 5m passes Gate 4
        "volume_1m_usd": 10.0,                    # $10/min < $100 floor
        "spike_peak_1m_vol": 2500.0,
        "initial_spike_resistance_price": 0.000100,
    }
    passed, reason = paper_engine.evaluate_candidate_gates(pool, quote=quote_dead_10usd)
    assert not passed
    assert "Baseline Demand Floor Gate" in reason


def test_gate_5_healthy_consolidation_above_baseline_demand_floor(paper_engine):
    """
    Healthy consolidation: 1m volume drops to $150/min (< 20% of $2,000 peak = $400),
    AND remains >= $100/min baseline demand floor.
    Breakout above resistance with buy volume > 1.5x consolidation average passes cleanly.
    """
    pool = {"token_mint": "MintHealthyConsol", "minutes_post_graduation": 10.0, "current_sol_reserve": 35.0}

    quote_healthy_breakout = {
        "price_sol": 0.000105,                    # Above resistance 0.000100
        "liquidity_usd": 6000.0,
        "quote_sol_reserve": 18.0,
        "total_txns_5m": 8,
        "buys_5m": 6,
        "sells_5m": 2,
        "price_change_5m": 4.5,
        "volume_5m_usd": 750.0,                   # 750 / 5 = 150/min
        "volume_1m_usd": 150.0,                   # $150/min: < $400 (<20% peak) AND >= $100 floor
        "buy_volume_1m_usd": 180.0,               # > 1.5 * 100 = 150
        "spike_peak_1m_vol": 2000.0,
        "initial_spike_resistance_price": 0.000100,
        "consolidation_avg_buy_vol": 100.0,
    }

    passed, reason = paper_engine.evaluate_candidate_gates(pool, quote=quote_healthy_breakout)
    assert passed
    assert "Passed All Candidate Gates" in reason


def test_market_structure_tracker_record_tick_dead_token_lifecycle():
    """
    Verifies MarketStructureTracker tick ingestion correctly marks dead tokens
    when volume drops below the baseline demand floor, rejecting subsequent whale spikes.
    """
    from datetime import datetime, timezone, timedelta
    from src.engine.market_structure import MarketStructureTracker

    tracker = MarketStructureTracker(min_baseline_demand_usd=100.0)
    t0 = datetime.now(timezone.utc)
    pool_addr = "PoolDeadTracker01"
    mint = "MintDeadTracker01"

    # Tick 1 (Minute 1): Initial spike phase
    ms = tracker.record_tick(
        pool_address=pool_addr,
        token_mint=mint,
        current_price_sol=0.0010,
        vol_1m_usd=3000.0,
        buy_vol_usd=2500.0,
        tick_time=t0 + timedelta(minutes=1),
        graduation_time=t0,
    )
    assert ms.spike_peak_1m_vol_usd == 3000.0
    assert not ms.is_dead_token

    # Tick 2 (Minute 4): Volume collapses to $15/min (abandoned retail)
    ms = tracker.record_tick(
        pool_address=pool_addr,
        token_mint=mint,
        current_price_sol=0.0008,
        vol_1m_usd=15.0,
        buy_vol_usd=5.0,
        tick_time=t0 + timedelta(minutes=4),
        graduation_time=t0,
    )
    assert ms.is_dead_token is True
    assert ms.consolidation_confirmed is False
    assert "1m volume $15/min < $100/min floor" in ms.dead_token_reason

    # Tick 3 (Minute 5): Whale sweeps $50 of liquidity, spiking price to 0.0015 (> 0.0010)
    ms = tracker.record_tick(
        pool_address=pool_addr,
        token_mint=mint,
        current_price_sol=0.0015,
        vol_1m_usd=50.0,
        buy_vol_usd=50.0,
        tick_time=t0 + timedelta(minutes=5),
        graduation_time=t0,
    )
    assert ms.breakout_confirmed is False

    # Evaluation must reject
    passed, reason = tracker.evaluate_structural_break(
        pool={"pool_address": pool_addr, "token_mint": mint, "minutes_post_graduation": 5.0},
        quote={"price_sol": 0.0015, "volume_1m_usd": 50.0},
    )
    assert not passed
    assert "Baseline Demand Floor Gate" in reason


def test_venue_gate_rejects_pumpfun_bonding_curve(paper_engine):
    """
    Verifies that raw pumpfun bonding curves are rejected by the Venue Gate.
    Only post-graduation AMM pools (PumpSwap, Raydium, Meteora) are allowed.
    """
    pool_pumpfun = {
        "token_mint": "MintPumpfun01",
        "pool_address": "PoolBondingCurve01",
        "minutes_post_graduation": 12.0,
        "current_sol_reserve": 35.0,
        "dex_name": "pumpfun",
    }
    quote_pumpfun = {
        "dex_id": "pumpfun",
        "price_sol": 0.00001,
        "liquidity_usd": 5000.0,
        "quote_sol_reserve": 16.0,
    }
    passed, reason = paper_engine.evaluate_candidate_gates(pool_pumpfun, quote=quote_pumpfun)
    assert not passed
    assert "Venue Gate" in reason
    assert "Raw bonding curve (pumpfun)" in reason


