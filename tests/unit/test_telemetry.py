"""
Unit tests for Phase 1 telemetry modules:
- Canonical pool discovery (SOL vs USDC)
- Post-graduation lifecycle and 8–25m secondary window tracking
- Rolling flow calculations and Path A+ friction modeling
"""

from datetime import datetime, timezone, timedelta
from src.telemetry.pool_discovery import PoolDiscoveryEngine, NATIVE_SOL_MINT
from src.telemetry.state_tracker import PoolStateTracker, PoolState
from src.telemetry.flow_telemetry import FlowTelemetryEngine, SwapEvent


def test_canonical_pool_discovery_filters_usdc():
    engine = PoolDiscoveryEngine()

    # Case 1: Non-canonical USDC pool -> should return None
    usdc_mint = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
    event_usdc = engine.parse_migration_instruction(
        tx_signature="sig123",
        slot=1000,
        timestamp_iso=datetime.now(timezone.utc).isoformat(),
        program_id=engine.pumpswap_program_id,
        accounts=["pool_addr_1", "token_mint_1", usdc_mint, "authority"],
        data_bytes=b"",
    )
    assert event_usdc is None, "Non-canonical USDC pools must be rejected"

    # Case 2: Canonical SOL pool -> should be captured
    event_sol = engine.parse_migration_instruction(
        tx_signature="sig456",
        slot=1001,
        timestamp_iso=datetime.now(timezone.utc).isoformat(),
        program_id=engine.pumpswap_program_id,
        accounts=["pool_addr_2", "token_mint_2", NATIVE_SOL_MINT, "authority"],
        data_bytes=b"",
    )
    assert event_sol is not None, "Canonical SOL pool must be accepted"
    assert event_sol.is_canonical is True
    assert event_sol.quote_mint == NATIVE_SOL_MINT


def test_state_tracker_enforces_cooling_and_active_windows():
    tracker = PoolStateTracker()

    # Simulate pool migration event
    from src.telemetry.pool_discovery import PoolMigrationEvent
    now = datetime.now(timezone.utc)

    # Pool 1: Age 4 minutes -> should be STABILIZING (<8m)
    event_young = PoolMigrationEvent(
        pool_address="pool_young",
        token_mint="mint_young",
        quote_mint=NATIVE_SOL_MINT,
        is_canonical=True,
        graduation_slot=2000,
        graduation_ts=(now - timedelta(minutes=4)).isoformat(),
        initial_sol_reserve=85.0,
        initial_token_reserve=206_900_000.0,
        market_cap_sol=410.0,
    )
    pool_young = tracker.on_pool_migration(event_young)
    state = tracker.update_pool_tick("pool_young")
    assert state == PoolState.STABILIZING, "Pool < 8 minutes must be in STABILIZING cooling period"

    # Pool 2: Age 12 minutes -> should be ACTIVE_WINDOW (8-25m)
    event_active = PoolMigrationEvent(
        pool_address="pool_active",
        token_mint="mint_active",
        quote_mint=NATIVE_SOL_MINT,
        is_canonical=True,
        graduation_slot=2001,
        graduation_ts=(now - timedelta(minutes=12)).isoformat(),
        initial_sol_reserve=95.0,
        initial_token_reserve=190_000_000.0,
        market_cap_sol=500.0,
    )
    pool_active = tracker.on_pool_migration(event_active)
    state_act = tracker.update_pool_tick("pool_active")
    assert state_act == PoolState.ACTIVE_WINDOW, "Pool at 12 minutes must be in ACTIVE_WINDOW"

    active_list = tracker.get_active_candidates()
    assert len(active_list) == 1
    assert active_list[0].pool_address == "pool_active"


def test_flow_telemetry_friction_model():
    engine = FlowTelemetryEngine()
    tracker = PoolStateTracker()
    now = datetime.now(timezone.utc)

    from src.telemetry.pool_discovery import PoolMigrationEvent
    event = PoolMigrationEvent(
        pool_address="pool_friction_test",
        token_mint="mint_friction",
        quote_mint=NATIVE_SOL_MINT,
        is_canonical=True,
        graduation_slot=3000,
        graduation_ts=(now - timedelta(minutes=10)).isoformat(),
        initial_sol_reserve=120.0,
        initial_token_reserve=180_000_000.0,
        market_cap_sol=650.0,
    )
    pool = tracker.on_pool_migration(event)

    # Record some swaps
    swap1 = SwapEvent(
        pool_address=pool.pool_address,
        is_buy=True,
        sol_amount=2.5,
        token_amount=3_500_000.0,
        trader_pubkey="buyer_wallet_1",
        timestamp=now - timedelta(seconds=15),
        slot=3005,
    )
    engine.record_swap(swap1, pool)

    metrics = engine.compute_rolling_metrics(pool)
    assert metrics.vol_60s_sol == 2.5
    assert metrics.price_impact_20usd_pct < 2.0, "120 SOL reserve should easily absorb $20 with <2% impact"
    assert metrics.is_depth_acceptable is True
    assert metrics.modeled_friction_pct <= 7.0, "Path A+ friction should stay below 7.0% cap"
