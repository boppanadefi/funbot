"""
Unit Tests for Phase 1: Virtual AMM Mirror, Data Staleness Circuit Breaker, and Event Bus.
Verifies:
1. Exact constant product pool simulation (x * y = k) instead of candle closes.
2. Local AMM mirror dictionary format: {"pool_address": {"sol_reserve": X, "token_reserve": Y, "k": X*Y}}.
3. Real-world swap event reserve recalculation before strategy evaluation.
4. Data Staleness Circuit Breaker (1000ms pause threshold).
5. Event Bus zero-latency pub/sub dispatching.
6. LivePaperTradingEngine execution gating under staleness.
"""

from datetime import datetime, timezone, timedelta
import json
import time
import pytest

from src.engine.event_bus import EventBus, StreamEventType, LiveSwapEvent, LiveAccountChangeEvent
from src.engine.staleness_breaker import DataStalenessCircuitBreaker
from src.engine.virtual_amm import VirtualPool, VirtualAMMMirror, SwapQuote
from src.engine.live_paper_trader import LivePaperTradingEngine
from src.telemetry.ws_stream import PumpSwapWSSClient


class TestVirtualPoolMath:
    """Tests exact constant-product AMM mechanics (x * y = k)."""

    def test_pool_initialization_invariant(self):
        pool = VirtualPool(
            pool_address="Pool1111111111111111111111111111111111111111",
            token_mint="Token1111111111111111111111111111111111111111",
            sol_reserve=84.0,
            token_reserve=206_900_000.0,
            venue_fee_pct=1.25,
        )
        assert pool.k == 84.0 * 206_900_000.0
        expected_marginal_price = 84.0 / 206_900_000.0
        assert abs(pool.marginal_price_sol - expected_marginal_price) < 1e-12

    def test_quote_swap_buy_exact_impact(self):
        pool = VirtualPool(
            pool_address="Pool111",
            token_mint="Token111",
            sol_reserve=84.0,
            token_reserve=206_900_000.0,
            venue_fee_pct=1.25,
        )
        quote = pool.quote_swap(sol_in=1.0, is_buy=True)
        assert quote.is_buy is True
        assert quote.sol_amount_in == 1.0
        assert quote.venue_fee_sol == 0.0125  # 1.25% of 1.0 SOL
        assert quote.token_amount_out > 0.0
        assert quote.marginal_price_after > quote.marginal_price_before
        assert quote.price_impact_pct > 0.0
        # Reserves in quote must reflect post-swap state
        assert quote.new_sol_reserve == 85.0

    def test_apply_swap_recalculates_reserves(self):
        pool = VirtualPool(
            pool_address="Pool222",
            token_mint="Token222",
            sol_reserve=84.0,
            token_reserve=206_900_000.0,
        )
        initial_k = pool.k
        # Buy swap of 2.0 SOL
        new_price = pool.apply_swap(is_buy=True, sol_amount=2.0, slot=100)
        assert pool.sol_reserve == 86.0
        assert pool.token_reserve < 206_900_000.0
        assert pool.last_slot == 100
        assert new_price > (84.0 / 206_900_000.0)
        assert pool.cumulative_swaps == 1
        assert pool.cumulative_buy_sol == 2.0

        # Sell swap of 1.0 SOL
        price_after_sell = pool.apply_swap(is_buy=False, sol_amount=1.0, slot=101)
        assert pool.sol_reserve < 86.0
        assert price_after_sell < new_price
        assert pool.cumulative_swaps == 2

    def test_apply_account_reserves_synchronization(self):
        pool = VirtualPool(
            pool_address="Pool333",
            token_mint="Token333",
            sol_reserve=84.0,
            token_reserve=206_900_000.0,
        )
        pool.apply_account_reserves(sol_reserve=92.5, token_reserve=188_000_000.0, slot=200)
        assert pool.sol_reserve == 92.5
        assert pool.token_reserve == 188_000_000.0
        assert pool.k == 92.5 * 188_000_000.0
        assert pool.last_slot == 200


class TestVirtualAMMMirror:
    """Tests the local dictionary AMM mirror format."""

    def test_local_dictionary_format(self):
        mirror = VirtualAMMMirror()
        mirror.register_pool(
            pool_address="PoolAlpha",
            token_mint="MintAlpha",
            initial_sol_reserve=85.0,
            initial_token_reserve=200_000_000.0,
        )

        # Dictionary format: {"pool_address": {"sol_reserve": X, "token_reserve": Y, "k": X*Y}}
        pools_dict = mirror.pools_dict
        assert "PoolAlpha" in pools_dict
        assert pools_dict["PoolAlpha"]["sol_reserve"] == 85.0
        assert pools_dict["PoolAlpha"]["token_reserve"] == 200_000_000.0
        assert pools_dict["PoolAlpha"]["k"] == 85.0 * 200_000_000.0

        # Direct index operator access
        pool_state = mirror["PoolAlpha"]
        assert pool_state["sol_reserve"] == 85.0
        assert pool_state["k"] == 85.0 * 200_000_000.0
        assert "PoolAlpha" in mirror

    def test_swap_event_processing_updates_pool(self):
        mirror = VirtualAMMMirror()
        mirror.register_pool(
            pool_address="PoolBeta",
            token_mint="MintBeta",
            initial_sol_reserve=80.0,
            initial_token_reserve=200_000_000.0,
        )

        updated_pool = mirror.process_swap_event(
            pool_address="PoolBeta",
            is_buy=True,
            sol_amount=5.0,
            slot=500,
        )
        assert updated_pool is not None
        assert updated_pool.sol_reserve == 85.0
        assert mirror["PoolBeta"]["sol_reserve"] == 85.0


class TestDataStalenessCircuitBreaker:
    """Tests 1000ms WebSocket latency threshold."""

    def test_starts_stale_until_message_received(self):
        breaker = DataStalenessCircuitBreaker(max_staleness_ms=1000.0)
        can_trade, reason = breaker.can_execute_decision()
        assert can_trade is False
        assert "TRIPPED" in reason

    def test_recovers_on_fresh_message(self):
        breaker = DataStalenessCircuitBreaker(max_staleness_ms=1000.0)
        breaker.record_message_received(slot=1000)
        can_trade, reason = breaker.can_execute_decision()
        assert can_trade is True
        assert breaker.is_stale is False
        assert breaker.last_slot == 1000

    def test_trips_when_exceeding_1000ms(self, monkeypatch):
        breaker = DataStalenessCircuitBreaker(max_staleness_ms=1000.0)
        base_time = time.monotonic()
        monkeypatch.setattr(time, "monotonic", lambda: base_time)
        breaker.record_message_received(slot=2000)
        assert breaker.can_execute_decision()[0] is True

        # Advance monotonic time by 1050ms (> 1000ms threshold)
        monkeypatch.setattr(time, "monotonic", lambda: base_time + 1.050)
        can_trade, reason = breaker.can_execute_decision()
        assert can_trade is False
        assert breaker.is_stale is True
        assert breaker.tripped_count == 1
        assert "TRIPPED" in reason

        # Fresh message arrives -> instant recovery
        breaker.record_message_received(slot=2001)
        assert breaker.can_execute_decision()[0] is True
        assert breaker.is_stale is False


class TestEventBus:
    """Tests zero-latency Pub/Sub dispatching."""

    def test_event_bus_pub_sub(self):
        bus = EventBus()
        received_swaps = []
        received_accounts = []

        bus.subscribe(StreamEventType.SWAP, lambda evt: received_swaps.append(evt))
        bus.subscribe(StreamEventType.ACCOUNT_CHANGE, lambda evt: received_accounts.append(evt))

        swap_evt = LiveSwapEvent(
            pool_address="PoolX",
            token_mint="MintX",
            is_buy=True,
            sol_amount=1.5,
            token_amount=1000.0,
            trader_pubkey="Trader123",
            slot=5000,
        )
        bus.publish(StreamEventType.SWAP, swap_evt)
        assert len(received_swaps) == 1
        assert received_swaps[0].pool_address == "PoolX"
        assert received_swaps[0].sol_amount == 1.5

        acc_evt = LiveAccountChangeEvent(
            pool_address="PoolX",
            sol_reserve=88.0,
            token_reserve=190_000_000.0,
            slot=5001,
        )
        bus.publish(StreamEventType.ACCOUNT_CHANGE, acc_evt)
        assert len(received_accounts) == 1
        assert received_accounts[0].sol_reserve == 88.0


class TestWSSClientMessageParsing:
    """Tests JSON-RPC parsing in PumpSwapWSSClient."""

    def test_logs_notification_parsing_and_amm_update(self):
        bus = EventBus()
        breaker = DataStalenessCircuitBreaker(max_staleness_ms=1000.0)
        mirror = VirtualAMMMirror()
        pool = mirror.register_pool(
            pool_address="6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P_Pool",
            token_mint="TokenTestPump",
            initial_sol_reserve=84.0,
            initial_token_reserve=206_900_000.0,
        )

        dispatched_events = []
        bus.subscribe(StreamEventType.SWAP, lambda evt: dispatched_events.append(evt))

        client = PumpSwapWSSClient(
            event_bus=bus,
            staleness_breaker=breaker,
            virtual_amm=mirror,
        )

        # Mock JSON-RPC logsNotification containing a Buy on PumpSwap program
        msg = {
            "jsonrpc": "2.0",
            "method": "logsNotification",
            "params": {
                "result": {
                    "context": {"slot": 305000000},
                    "value": {
                        "signature": "5KxyzSignature1234567890",
                        "logs": [
                            "Program 6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P invoke [1]",
                            "Program log: Instruction: Buy",
                            "Program 6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P success"
                        ]
                    }
                }
            }
        }
        client._handle_raw_message(json.dumps(msg))

        # Staleness breaker must be reset
        assert breaker.is_stale is False
        assert breaker.last_slot == 305000000

        # AMM reserves must be recalculated BEFORE dispatching event
        assert pool.sol_reserve > 84.0
        assert len(dispatched_events) == 1
        assert dispatched_events[0].is_buy is True


class TestLivePaperTraderPhase1Integration:
    """Tests LivePaperTradingEngine integration with Staleness Breaker and Virtual AMM."""

    def test_engine_pauses_when_stream_is_stale(self, tmp_path):
        db_path = str(tmp_path / "test.db")
        state_file = str(tmp_path / "active_pos.json")

        breaker = DataStalenessCircuitBreaker(max_staleness_ms=1000.0)
        # Breaker starts stale
        engine = LivePaperTradingEngine(
            db_path=db_path,
            state_file=state_file,
            staleness_breaker=breaker,
            enforce_staleness_breaker=True,
        )

        dummy_pools = [{
            "pool_address": "TestPoolStale",
            "token_mint": "TestMintStale",
            "current_sol_reserve": 85.0,
            "current_token_reserve": 200_000_000.0,
            "state": "ACTIVE_WINDOW",
            "age_minutes": 10.0,
        }]

        # Tick must be paused by Data Staleness Circuit Breaker
        tick_result = engine.on_market_tick(dummy_pools)
        assert tick_result is not None
        assert tick_result["status"] == "PAUSED_DATA_STALE"

    def test_engine_processes_tick_when_stream_is_fresh(self, tmp_path):
        db_path = str(tmp_path / "test.db")
        state_file = str(tmp_path / "active_pos.json")

        breaker = DataStalenessCircuitBreaker(max_staleness_ms=1000.0)
        breaker.record_message_received(slot=100)  # Fresh stream

        engine = LivePaperTradingEngine(
            db_path=db_path,
            state_file=state_file,
            staleness_breaker=breaker,
            enforce_staleness_breaker=True,
        )

        dummy_pools = [{
            "pool_address": "TestPoolFresh",
            "token_mint": "TestMintFresh",
            "current_sol_reserve": 85.0,
            "current_token_reserve": 200_000_000.0,
            "state": "ACTIVE_WINDOW",
            "age_minutes": 10.0,
        }]

        # Does not return PAUSED_DATA_STALE
        tick_result = engine.on_market_tick(dummy_pools)
        assert tick_result is None or tick_result.get("status") != "PAUSED_DATA_STALE"
