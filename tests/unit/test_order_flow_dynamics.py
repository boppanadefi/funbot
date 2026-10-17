"""
Unit Tests for Fix 4: Real-Time Order Flow Dynamics & Wash Trading Defense.
Validates:
1. Velocity of Unique Wallets (VUW >= 1.20).
2. Aggression Ratio (Taker Buy SOL / Taker Sell SOL > 2.00).
3. Rejection of flat wash-trader wallet rotation and balanced wash trading.
4. Acceptance of organic retail FOMO acceleration.
5. Integration into LivePaperTradingEngine candidate gates.
"""

from datetime import datetime, timezone, timedelta
import pytest

from src.engine.order_flow import OrderFlowDynamicsEngine, OrderFlowMetrics
from src.engine.live_paper_trader import LivePaperTradingEngine
from src.cluster.risk_scoring import ClusterRiskEngine
from src.cluster.models import WalletProfile
from src.live.circuit_breaker import DailyCircuitBreaker
from src.live.kill_switch import EmergencyKillSwitch
from src.telemetry.pool_discovery import NATIVE_SOL_MINT


def test_vuw_calculation_and_hard_reject():
    engine = OrderFlowDynamicsEngine(min_vuw_ratio=1.20)

    # 1. Flat wash trading rotation (rotating same 8 wallets: 8 cur / 8 prior = 1.00 < 1.20)
    vuw_flat = engine.compute_vuw(current_1m_buyers=8, prior_1m_buyers=8)
    assert vuw_flat == 1.00
    metrics_flat = engine.evaluate_order_flow(
        pool_address="PoolWash01",
        token_mint="MintWash01",
        unique_buyers_current_1m=8,
        unique_buyers_prior_1m=8,
        taker_buy_sol=10.0,
        taker_sell_sol=2.0,  # Aggression is fine (5.0), but VUW fails!
    )
    assert metrics_flat.is_approved is False
    assert metrics_flat.classification == "WASH_TRADING_SYBIL"
    assert any("VUW Rejected" in r for r in metrics_flat.rejection_reasons)

    # 2. Accelerating organic retail FOMO (15 cur / 8 prior = 1.875 >= 1.20)
    vuw_accel = engine.compute_vuw(current_1m_buyers=15, prior_1m_buyers=8)
    assert vuw_accel == 1.875
    metrics_accel = engine.evaluate_order_flow(
        pool_address="PoolFOMO01",
        token_mint="MintFOMO01",
        unique_buyers_current_1m=15,
        unique_buyers_prior_1m=8,
        taker_buy_sol=12.0,
        taker_sell_sol=3.0,  # Aggression 4.0 > 2.0
    )
    assert metrics_accel.is_approved is True
    assert metrics_accel.classification == "ORGANIC_RETAIL"
    assert len(metrics_accel.rejection_reasons) == 0

    # 3. Stagnant buyers fading (3 cur / 6 prior = 0.50 < 1.20)
    vuw_fading = engine.compute_vuw(current_1m_buyers=3, prior_1m_buyers=6)
    assert vuw_fading == 0.50
    assert vuw_fading < 1.20


def test_aggression_ratio_calculation_and_threshold():
    engine = OrderFlowDynamicsEngine(min_aggression_ratio=2.00)

    # 1. Balanced wash trading (5.0 SOL buy / 5.0 SOL sell = 1.00 <= 2.00)
    agg_balanced = engine.compute_aggression_ratio(taker_buy_sol=5.0, taker_sell_sol=5.0)
    assert agg_balanced == 1.00
    metrics_balanced = engine.evaluate_order_flow(
        pool_address="PoolWash02",
        token_mint="MintWash02",
        unique_buyers_current_1m=15,
        unique_buyers_prior_1m=10,  # VUW 1.50 passes, but Aggression fails!
        taker_buy_sol=5.0,
        taker_sell_sol=5.0,
    )
    assert metrics_balanced.is_approved is False
    assert any("Aggression Ratio Rejected" in r for r in metrics_balanced.rejection_reasons)

    # 2. Weak buy aggression (3.0 SOL buy / 2.0 SOL sell = 1.50 <= 2.00)
    agg_weak = engine.compute_aggression_ratio(taker_buy_sol=3.0, taker_sell_sol=2.0)
    assert agg_weak == 1.50

    # 3. Strong organic buy-led breakout (14.0 SOL buy / 3.5 SOL sell = 4.00 > 2.00)
    agg_strong = engine.compute_aggression_ratio(taker_buy_sol=14.0, taker_sell_sol=3.5)
    assert agg_strong == 4.00

    # 4. Pure market buys (zero sells)
    agg_pure = engine.compute_aggression_ratio(taker_buy_sol=10.0, taker_sell_sol=0.0)
    assert agg_pure == 999.0
    assert agg_pure > 2.00


def test_swap_history_partitioning_into_order_flow():
    engine = OrderFlowDynamicsEngine()
    now = datetime(2026, 10, 17, 12, 0, 0, tzinfo=timezone.utc)

    # Swaps in prior minute [11:58:00 to 11:59:00]: 3 unique buyers
    prior_swaps = [
        {"trader_pubkey": "BuyerPrior1", "is_buy": True, "sol_amount": 1.0, "timestamp": now - timedelta(seconds=90)},
        {"trader_pubkey": "BuyerPrior2", "is_buy": True, "sol_amount": 1.5, "timestamp": now - timedelta(seconds=80)},
        {"trader_pubkey": "BuyerPrior3", "is_buy": True, "sol_amount": 0.8, "timestamp": now - timedelta(seconds=70)},
    ]

    # Swaps in current minute [11:59:00 to 12:00:00]: 6 unique buyers (accelerating!), 12 SOL buys, 2 SOL sells
    current_swaps = [
        {"trader_pubkey": f"BuyerCur{i}", "is_buy": True, "sol_amount": 2.0, "timestamp": now - timedelta(seconds=50 - i * 8)}
        for i in range(6)
    ]
    current_swaps.append(
        {"trader_pubkey": "Seller1", "is_buy": False, "sol_amount": 2.0, "timestamp": now - timedelta(seconds=20)}
    )

    all_swaps = prior_swaps + current_swaps

    metrics = engine.evaluate_order_flow(
        pool_address="PoolDynamic01",
        token_mint="MintDynamic01",
        swaps=all_swaps,
        now=now,
    )

    # VUW = 6 / 3 = 2.00 >= 1.20
    assert metrics.unique_buyers_current_1m == 6
    assert metrics.unique_buyers_prior_1m == 3
    assert metrics.vuw == 2.00
    # Aggression = 12.0 SOL / 2.0 SOL = 6.00 > 2.00
    assert metrics.taker_buy_sol == 12.0
    assert metrics.taker_sell_sol == 2.0
    assert metrics.aggression_ratio == 6.00
    assert metrics.is_approved is True
    assert metrics.classification == "ORGANIC_RETAIL"


def test_live_paper_trader_candidate_gates_order_flow(tmp_path):
    db_file = tmp_path / "test_of_gates.db"
    state_file = tmp_path / "active_pos.json"
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

    base_pool = {
        "pool_address": "PoolOF1",
        "token_mint": "MintOF1",
        "minutes_post_graduation": 10.0,
        "current_sol_reserve": 35.0,
    }

    # Case 1: Passes Gate 1-5, but VUW = 1.0 (Wash trading rotation)
    quote_wash_vuw = {
        "price_sol": 0.000105,
        "liquidity_usd": 6000.0,
        "quote_sol_reserve": 18.0,
        "total_txns_5m": 8,
        "buys_5m": 6,
        "sells_5m": 2,
        "price_change_5m": 3.2,
        "volume_5m_usd": 1000.0,
        "volume_1m_usd": 200.0,
        "buy_volume_1m_usd": 150.0,
        "spike_peak_1m_vol": 2000.0,
        "initial_spike_resistance_price": 0.000100,
        "consolidation_avg_buy_vol": 50.0,
        "unique_buyers_current_1m": 5,
        "unique_buyers_prior_1m": 5,  # VUW = 1.00 < 1.20
        "taker_buy_sol": 10.0,
        "taker_sell_sol": 2.0,
    }
    passed, reason = engine.evaluate_candidate_gates(base_pool, quote=quote_wash_vuw)
    assert not passed
    assert "Order Flow Dynamics Gate" in reason
    assert "VUW Rejected" in reason

    # Case 2: VUW = 1.6 (passes), but Aggression Ratio = 1.25 <= 2.0 (balanced wash trading)
    quote_weak_agg = {
        "price_sol": 0.000105,
        "liquidity_usd": 6000.0,
        "quote_sol_reserve": 18.0,
        "total_txns_5m": 8,
        "buys_5m": 6,
        "sells_5m": 2,
        "price_change_5m": 3.2,
        "volume_5m_usd": 1000.0,
        "volume_1m_usd": 200.0,
        "buy_volume_1m_usd": 150.0,
        "spike_peak_1m_vol": 2000.0,
        "initial_spike_resistance_price": 0.000100,
        "consolidation_avg_buy_vol": 50.0,
        "unique_buyers_current_1m": 8,
        "unique_buyers_prior_1m": 5,  # VUW = 1.60 (passes)
        "taker_buy_sol": 5.0,
        "taker_sell_sol": 4.0,  # Aggression = 1.25 <= 2.0 (fails)
    }
    passed, reason = engine.evaluate_candidate_gates(base_pool, quote=quote_weak_agg)
    assert not passed
    assert "Order Flow Dynamics Gate" in reason
    assert "Aggression Ratio Rejected" in reason

    # Case 3: Fully validated breakout: VUW = 2.0 and Aggression = 4.0
    quote_full_pass = {
        "price_sol": 0.000105,
        "liquidity_usd": 6000.0,
        "quote_sol_reserve": 18.0,
        "total_txns_5m": 8,
        "buys_5m": 7,
        "sells_5m": 1,
        "price_change_5m": 3.2,
        "volume_5m_usd": 1000.0,
        "volume_1m_usd": 200.0,
        "buy_volume_1m_usd": 150.0,
        "spike_peak_1m_vol": 2000.0,
        "initial_spike_resistance_price": 0.000100,
        "consolidation_avg_buy_vol": 50.0,
        "unique_buyers_current_1m": 10,
        "unique_buyers_prior_1m": 5,   # VUW = 2.00 >= 1.20
        "taker_buy_sol": 12.0,
        "taker_sell_sol": 3.0,   # Aggression = 4.00 > 2.00
    }
    passed, reason = engine.evaluate_candidate_gates(base_pool, quote=quote_full_pass)
    assert passed
    assert "Passed All Candidate Gates" in reason


def test_cluster_risk_engine_integrates_order_flow():
    engine = ClusterRiskEngine()

    safe_buyers = [
        WalletProfile(pubkey=f"SafeBuyer{i}", pre_trade_tx_count=20, total_sol_bought=1.0)
        for i in range(10)
    ]
    
    # Swaps showing aggressive organic buying: all 10 buys, zero sells
    swaps = [
        {"trader_pubkey": f"SafeBuyer{i}", "sol_amount": float(i + 1), "slot": 300_000_000 + i * 10, "is_buy": True}
        for i in range(10)
    ]

    score = engine.evaluate_candidate(
        pool_address="PoolSafeOrderFlow",
        token_mint="TokenMintSafeOF",
        buyer_profiles=safe_buyers,
        swaps=swaps,
        mint_authority=None,
        freeze_authority=None,
        quote_mint=NATIVE_SOL_MINT,
    )

    assert score.is_approved is True
    assert score.order_flow_passed is True
    assert score.vuw >= 1.20
    assert score.aggression_ratio > 2.00
    assert score.composite_risk_score <= 0.40


def test_syndicate_1m_spoof_defeated_by_5_block_delta_buyers():
    """
    Fix 4: Syndicate fires 20 fresh wallets in block N (making 1m VUW appear high ~2.0).
    However, across blocks N+1 to N+5 (the 5-block real-time window), 0 new buyers enter.
    Slot-level order flow requires Delta Unique Buyers >= 1 over the last 5 blocks,
    instantly defeating the syndicate spoof trap before they can dump in N+2.
    """
    engine = OrderFlowDynamicsEngine(min_delta_unique_buyers_5blocks=1)

    # Syndicate spoof: 1m VUW looks high, but 5-block delta is 0
    metrics_spoof = engine.evaluate_order_flow(
        pool_address="PoolSyndicateSpoof",
        token_mint="MintSyndicateSpoof",
        unique_buyers_current_1m=20,
        unique_buyers_prior_1m=10,  # 1m VUW = 2.00 (would trick legacy 1m window)
        delta_unique_buyers_5blocks=0,  # 0 new buyers in the last 5 blocks!
        taker_buy_sol=15.0,
        taker_sell_sol=2.0,
    )
    assert metrics_spoof.is_approved is False
    assert metrics_spoof.delta_unique_buyers_5blocks == 0
    assert any("Delta Unique Buyers" in r for r in metrics_spoof.rejection_reasons)

    # Organic retail continuation: 3 new buyers entered across the last 5 blocks
    metrics_organic = engine.evaluate_order_flow(
        pool_address="PoolOrganicRealtime",
        token_mint="MintOrganicRealtime",
        unique_buyers_current_1m=20,
        unique_buyers_prior_1m=10,
        delta_unique_buyers_5blocks=3,  # Delta >= 1
        taker_buy_sol=15.0,
        taker_sell_sol=2.0,             # 15 / 17 = 88.2% > 70%
    )
    assert metrics_organic.is_approved is True
    assert metrics_organic.delta_unique_buyers_5blocks == 3
    assert len(metrics_organic.rejection_reasons) == 0


def test_breakout_candle_taker_buy_dominance_threshold():
    """
    Fix 4: Instead of 1m Aggression Ratio, require that >70% of the SOL moved
    in the breakout candle was Taker Buy.
    """
    engine = OrderFlowDynamicsEngine(min_breakout_taker_buy_pct=70.0)

    # Case A: 6.5 SOL Taker Buy / 3.5 SOL Taker Sell = 65.0% <= 70.0% (Rejects)
    pct_fail = engine.compute_breakout_taker_buy_pct(taker_buy_sol=6.5, taker_sell_sol=3.5)
    assert pct_fail == 65.0
    metrics_fail = engine.evaluate_order_flow(
        pool_address="PoolDominanceFail",
        token_mint="MintDominanceFail",
        delta_unique_buyers_5blocks=2,
        taker_buy_sol=6.5,
        taker_sell_sol=3.5,
    )
    assert metrics_fail.is_approved is False
    assert metrics_fail.breakout_taker_buy_pct == 65.0
    assert any("Taker Buy dominance" in r for r in metrics_fail.rejection_reasons)

    # Case B: 8.5 SOL Taker Buy / 1.5 SOL Taker Sell = 85.0% > 70.0% (Passes)
    pct_pass = engine.compute_breakout_taker_buy_pct(taker_buy_sol=8.5, taker_sell_sol=1.5)
    assert pct_pass == 85.0
    metrics_pass = engine.evaluate_order_flow(
        pool_address="PoolDominancePass",
        token_mint="MintDominancePass",
        delta_unique_buyers_5blocks=2,
        taker_buy_sol=8.5,
        taker_sell_sol=1.5,
    )
    assert metrics_pass.is_approved is True
    assert metrics_pass.breakout_taker_buy_pct == 85.0
    assert len(metrics_pass.rejection_reasons) == 0


def test_slot_level_candidate_gate_live_engine(tmp_path):
    """
    Verifies that LivePaperTradingEngine correctly applies the slot-level order flow rules:
    - Rejects if delta unique buyers over last 5 blocks < 1
    - Rejects if breakout candle taker buy <= 70%
    """
    db_file = tmp_path / "test_slot_gates.db"
    state_file = tmp_path / "slot_state.json"

    engine = LivePaperTradingEngine(
        db_path=str(db_file),
        state_file=str(state_file),
    )

    base_pool = {
        "pool_address": "PoolSlotGate01",
        "token_mint": "MintSlotGate01",
        "minutes_post_graduation": 10.0,
        "current_sol_reserve": 35.0,
    }

    # Case 1: Rejects when 5-block delta is 0
    quote_zero_delta = {
        "price_sol": 0.000105,
        "liquidity_usd": 6000.0,
        "quote_sol_reserve": 18.0,
        "total_txns_5m": 8,
        "buys_5m": 6,
        "sells_5m": 2,
        "price_change_5m": 3.2,
        "volume_5m_usd": 1000.0,
        "volume_1m_usd": 200.0,
        "buy_volume_1m_usd": 150.0,
        "spike_peak_1m_vol": 2000.0,
        "initial_spike_resistance_price": 0.000100,
        "consolidation_avg_buy_vol": 50.0,
        "delta_unique_buyers_5blocks": 0,       # 0 new buyers in last 5 blocks!
        "taker_buy_sol": 12.0,
        "taker_sell_sol": 2.0,
    }
    passed, reason = engine.evaluate_candidate_gates(base_pool, quote=quote_zero_delta)
    assert not passed
    assert "Delta Unique Buyers" in reason

    # Case 2: Rejects when breakout candle Taker Buy is 60% (<= 70%)
    quote_low_buy_dominance = {
        "price_sol": 0.000105,
        "liquidity_usd": 6000.0,
        "quote_sol_reserve": 18.0,
        "total_txns_5m": 8,
        "buys_5m": 6,
        "sells_5m": 2,
        "price_change_5m": 3.2,
        "volume_5m_usd": 1000.0,
        "volume_1m_usd": 200.0,
        "buy_volume_1m_usd": 150.0,
        "spike_peak_1m_vol": 2000.0,
        "initial_spike_resistance_price": 0.000100,
        "consolidation_avg_buy_vol": 50.0,
        "delta_unique_buyers_5blocks": 2,
        "taker_buy_sol": 6.0,
        "taker_sell_sol": 4.0,                  # 6 / 10 = 60.0% <= 70.0%
    }
    passed, reason = engine.evaluate_candidate_gates(base_pool, quote=quote_low_buy_dominance)
    assert not passed
    assert "Taker Buy dominance" in reason or "Breakout Candle Taker Buy Gate" in reason

