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
        min_age_minutes=8.0,
        max_age_minutes=25.0,
        circuit_breaker=cb,
        kill_switch=ks,
    )


def test_gate_1_pool_age_default_window(paper_engine):
    pool_young = {"token_mint": "Mint1", "minutes_post_graduation": 5.0, "current_sol_reserve": 35.0}
    passed, reason = paper_engine.evaluate_candidate_gates(pool_young, quote=None)
    assert not passed
    assert "Pool Age Gate" in reason

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

    # Case C: All 4 gates pass
    quote_valid = {
        "price_sol": 0.0001,
        "liquidity_usd": 6000.0,
        "quote_sol_reserve": 18.0,
        "total_txns_5m": 6,
        "buys_5m": 4,
        "sells_5m": 2,
        "price_change_5m": 3.2,
        "volume_5m_usd": 750.0,
    }
    passed, reason = paper_engine.evaluate_candidate_gates(pool, quote=quote_valid)
    assert passed
    assert reason == "Passed All 4 Gates"


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
