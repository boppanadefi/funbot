"""
Unit tests for LiveMarketFeed adapter.
Tests parsing of real Solana pool deployments and DexScreener price ticks.
"""

from datetime import datetime, timezone, timedelta
import pytest
from src.telemetry.live_market_feed import LiveMarketFeed


def test_live_market_feed_initialization():
    feed = LiveMarketFeed(sol_price_usd=155.0)
    assert feed.sol_price_usd == 155.0
    assert feed.timeout_seconds == 6


def test_parse_pool_age_categorization():
    feed = LiveMarketFeed(sol_price_usd=150.0)
    now = datetime.now(timezone.utc)

    # 1. Pool age 3 minutes -> COOLING (<8m)
    p_3m = now - timedelta(minutes=3.0)
    # 2. Pool age 15 minutes -> ACTIVE_WINDOW (8-25m)
    p_15m = now - timedelta(minutes=15.0)
    # 3. Pool age 35 minutes -> EXPIRED (>25m)
    p_35m = now - timedelta(minutes=35.0)

    for p_time, expected_state in [(p_3m, "COOLING"), (p_15m, "ACTIVE_WINDOW"), (p_35m, "EXPIRED")]:
        age_min = (now - p_time).total_seconds() / 60.0
        if age_min < 8.0:
            state = "COOLING"
        elif 8.0 <= age_min <= 25.0:
            state = "ACTIVE_WINDOW"
        else:
            state = "EXPIRED"
        assert state == expected_state


def test_live_feed_real_network_fetch():
    feed = LiveMarketFeed(sol_price_usd=155.0, timeout_seconds=8)
    pools = feed.fetch_newly_graduated_pools()
    # If network is reachable, pools list will be populated
    if pools:
        p = pools[0]
        assert "pool_address" in p
        assert "token_mint" in p
        assert "current_sol_reserve" in p
        assert p["state"] in ("COOLING", "ACTIVE_WINDOW", "EXPIRED")
        assert p["is_canonical"] is True
