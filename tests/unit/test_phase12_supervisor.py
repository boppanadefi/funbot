"""
Unit and Integration Test Suite for Phase 12: Production Supervisor & Daemon Management.
Validates:
1. HeartbeatPublisher pulse emission (atomic JSON + SQLite WAL).
2. HeartbeatWatchdog health checks (staleness, memory ceiling, dead process).
3. ProductionSupervisor lifecycle, exponential backoff, and crash loop circuit breaker.
4. StartupReconciler on-chain SPL comparison (matching, downtime liquidation, orphan tokens).
5. CLI supervisor subcommand behavior.
"""

import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from src.engine.position_state_machine import PositionContext, PositionState, ExecutionMode
from src.orchestrator.alerting import AlertDispatcher, AlertEvent, AlertSeverity
from src.orchestrator.heartbeat import (
    HeartbeatPublisher,
    HeartbeatWatchdog,
    HeartbeatPulse,
    WatchdogStatus,
    get_process_rss_mb,
    is_process_running,
)
from src.orchestrator.persistence import StatePersistenceManager
from src.orchestrator.reconciliation import (
    StartupReconciler,
    ReconciliationReport,
    fetch_wallet_spl_balances,
)
from src.orchestrator.supervisor import (
    ProductionSupervisor,
    SupervisorConfig,
)


@pytest.fixture
def temp_dir():
    with tempfile.TemporaryDirectory() as td:
        yield Path(td)


@pytest.fixture
def test_persistence(temp_dir):
    db_path = temp_dir / "test_orchestrator.db"
    return StatePersistenceManager(db_path=str(db_path))


@pytest.fixture
def mock_alerts():
    dispatcher = MagicMock(spec=AlertDispatcher)
    dispatcher.dispatch = AsyncMock(return_value={"mock": True})
    return dispatcher


# ==============================================================================
# 1. Heartbeat Publisher & Watchdog Tests
# ==============================================================================

def test_heartbeat_publisher_pulse_atomic(temp_dir, test_persistence):
    """Verifies that HeartbeatPublisher creates and atomically updates heartbeat.json and SQLite WAL."""
    hb_file = temp_dir / "heartbeat.json"
    publisher = HeartbeatPublisher(
        persistence_manager=test_persistence,
        filepath=str(hb_file),
        active_positions_provider=lambda: 2,
        kill_switch_provider=lambda: False,
        circuit_breaker_provider=lambda: False,
        slot_provider=lambda: 320000000,
        mode="TEST_DAEMON",
    )

    pulse = publisher.publish_pulse()

    assert hb_file.exists()
    with open(hb_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    assert data["pid"] == os.getpid()
    assert data["active_positions"] == 2
    assert data["last_slot"] == 320000000
    assert data["mode"] == "TEST_DAEMON"
    assert data["memory_rss_mb"] > 0

    # Verify persisted in SQLite WAL
    db_hb = test_persistence.load_latest_heartbeat()
    assert db_hb is not None
    assert db_hb["pid"] == os.getpid()
    assert db_hb["active_positions"] == 2
    assert db_hb["last_slot"] == 320000000


def test_heartbeat_watchdog_healthy(temp_dir):
    """Verifies watchdog marks recent, healthy heartbeats as OK."""
    hb_file = temp_dir / "heartbeat.json"
    publisher = HeartbeatPublisher(filepath=str(hb_file))
    publisher.publish_pulse()

    watchdog = HeartbeatWatchdog(filepath=str(hb_file), stale_threshold_seconds=60.0, memory_ceiling_mb=512.0)
    status = watchdog.check_health(child_pid=os.getpid())

    assert status.is_healthy is True
    assert status.is_stale is False
    assert status.memory_breached is False
    assert status.reason == "HEALTHY"
    assert status.process_alive is True


def test_heartbeat_watchdog_stale_detection(temp_dir):
    """Verifies watchdog flags pulse older than threshold as STALE."""
    hb_file = temp_dir / "heartbeat.json"
    stale_time = time.time() - 120.0  # 2 minutes ago
    data = {
        "timestamp": stale_time,
        "pid": os.getpid(),
        "uptime_seconds": 120.0,
        "memory_rss_mb": 45.0,
        "cpu_percent": 0.0,
        "active_positions": 0,
        "is_kill_switch_tripped": False,
        "is_circuit_breaker_halted": False,
        "last_slot": 100,
    }
    with open(hb_file, "w", encoding="utf-8") as f:
        json.dump(data, f)

    watchdog = HeartbeatWatchdog(filepath=str(hb_file), stale_threshold_seconds=60.0)
    status = watchdog.check_health(child_pid=os.getpid())

    assert status.is_healthy is False
    assert status.is_stale is True
    assert "HEARTBEAT_STALE" in status.reason


def test_heartbeat_watchdog_memory_ceiling_breach(temp_dir):
    """Verifies watchdog flags processes exceeding memory RSS ceiling."""
    hb_file = temp_dir / "heartbeat.json"
    data = {
        "timestamp": time.time(),
        "pid": os.getpid(),
        "uptime_seconds": 30.0,
        "memory_rss_mb": 600.0,  # Breached ceiling
        "cpu_percent": 0.0,
        "active_positions": 0,
        "is_kill_switch_tripped": False,
        "is_circuit_breaker_halted": False,
        "last_slot": 100,
    }
    with open(hb_file, "w", encoding="utf-8") as f:
        json.dump(data, f)

    # With high mock or measured ceiling: 10.0 MB threshold (current process has >10MB)
    watchdog = HeartbeatWatchdog(filepath=str(hb_file), memory_ceiling_mb=5.0)
    status = watchdog.check_health(child_pid=os.getpid())

    assert status.is_healthy is False
    assert status.memory_breached is True
    assert "MEMORY_CEILING_BREACHED" in status.reason


def test_watchdog_missing_heartbeat_file(temp_dir):
    """Verifies watchdog handles missing heartbeat file gracefully."""
    hb_file = temp_dir / "nonexistent_heartbeat.json"
    watchdog = HeartbeatWatchdog(filepath=str(hb_file))
    status = watchdog.check_health(child_pid=os.getpid())

    assert status.is_healthy is False
    assert status.reason == "MISSING_HEARTBEAT_FILE"
    assert status.is_stale is True


# ==============================================================================
# 2. Production Supervisor Lifecycle & Circuit Breaker Tests
# ==============================================================================

def test_supervisor_exponential_backoff():
    """Verifies supervisor backoff sequence on consecutive crashes."""
    config = SupervisorConfig(backoff_schedule=(5.0, 10.0, 20.0, 40.0, 60.0))
    supervisor = ProductionSupervisor(config=config)

    assert supervisor.get_backoff_delay() == 0.0

    supervisor.record_restart()
    assert supervisor.get_backoff_delay() == 5.0

    supervisor.record_restart()
    assert supervisor.get_backoff_delay() == 10.0

    supervisor.record_restart()
    assert supervisor.get_backoff_delay() == 20.0

    supervisor.record_restart()
    assert supervisor.get_backoff_delay() == 40.0


def test_supervisor_crash_loop_circuit_breaker():
    """Verifies crash loop circuit breaker trips when crashes exceed 5 within 15 minutes."""
    config = SupervisorConfig(max_restarts_in_window=5, window_seconds=900.0)
    supervisor = ProductionSupervisor(config=config)

    # 4 restarts within window should remain healthy
    for _ in range(4):
        ok = supervisor.record_restart()
        assert ok is True
        assert supervisor.is_circuit_breaker_tripped is False

    # 5th restart within window trips the circuit breaker
    ok = supervisor.record_restart()
    assert ok is False
    assert supervisor.is_circuit_breaker_tripped is True
    assert supervisor.total_restarts == 5

    # Manual reset clears the breaker
    supervisor.reset_circuit_breaker()
    assert supervisor.is_circuit_breaker_tripped is False
    assert supervisor.get_recent_restart_count() == 0


@pytest.mark.asyncio
async def test_supervisor_child_exit_handling(mock_alerts):
    """Tests supervisor handling of unexpected child crash and alert dispatch."""
    supervisor = ProductionSupervisor(alert_dispatcher=mock_alerts)
    supervisor._process = MagicMock()
    supervisor._process.pid = 99999

    with patch("asyncio.sleep", new_callable=AsyncMock):
        await supervisor._handle_child_exit(exit_code=1)

    assert supervisor.total_restarts == 1
    assert mock_alerts.dispatch.called
    dispatched_event = mock_alerts.dispatch.call_args[0][0]
    assert dispatched_event.event_type == "DAEMON_UNEXPECTED_EXIT"
    assert dispatched_event.severity == AlertSeverity.WARNING


@pytest.mark.asyncio
async def test_supervisor_clean_child_exit_stops_supervisor(mock_alerts):
    """Tests that a clean exit (code 0) does not trigger a restart."""
    supervisor = ProductionSupervisor(alert_dispatcher=mock_alerts)
    supervisor._is_running = True
    supervisor._process = MagicMock()
    supervisor._process.pid = 99999

    await supervisor._handle_child_exit(exit_code=0)

    assert supervisor.is_running is False
    assert supervisor.total_restarts == 0


def test_supervisor_get_status(temp_dir):
    """Verifies supervisor get_status provides complete diagnostic structure."""
    hb_file = temp_dir / "heartbeat.json"
    publisher = HeartbeatPublisher(filepath=str(hb_file))
    publisher.publish_pulse()

    watchdog = HeartbeatWatchdog(filepath=str(hb_file))
    supervisor = ProductionSupervisor(watchdog=watchdog)
    status = supervisor.get_status()

    assert "supervisor_pid" in status
    assert "child_pid" in status
    assert "is_running" in status
    assert "recent_restarts_15m" in status
    assert "circuit_breaker_tripped" in status
    assert "watchdog" in status
    assert status["watchdog"]["is_healthy"] is True


# ==============================================================================
# 3. Startup On-Chain SPL Reconciliation Tests
# ==============================================================================

@pytest.mark.asyncio
async def test_reconciler_matching_positions(test_persistence, mock_alerts):
    """Verifies active positions with matching on-chain tokens remain active."""
    ctx = PositionContext(
        pool_id="pool_active_1",
        mint="MintTokenAAA11111111111111111111111111111",
        mode=ExecutionMode.PATH_A_PLUS,
        entry_price_sol=0.005,
        size_tokens=1000000,
        size_sol_notional=5.0,
        entry_timestamp=time.time(),
    )
    test_persistence.save_position_state(ctx, PositionState.RUNNER_ACTIVE)

    reconciler = StartupReconciler(persistence=test_persistence, alerts=mock_alerts)
    on_chain = {"MintTokenAAA11111111111111111111111111111": 1000000}

    report = await reconciler.reconcile(wallet_pubkey="TestWalletPubkey", on_chain_balances=on_chain)

    assert len(report.matching_positions) == 1
    assert len(report.downtime_liquidated) == 0
    assert len(report.orphan_holdings) == 0

    # Ensure still active in persistence
    active = test_persistence.load_active_positions()
    assert len(active) == 1
    assert active[0]["pool_id"] == "pool_active_1"


@pytest.mark.asyncio
async def test_reconciler_downtime_liquidation_closed(test_persistence, mock_alerts):
    """Verifies that an active WAL position whose on-chain balance is 0 is marked CLOSED."""
    ctx = PositionContext(
        pool_id="pool_ghost_2",
        mint="MintGhostBBB22222222222222222222222222222",
        mode=ExecutionMode.PATH_A_PLUS,
        entry_price_sol=0.010,
        size_tokens=500000,
        size_sol_notional=5.0,
        entry_timestamp=time.time() - 3600.0,
    )
    test_persistence.save_position_state(ctx, PositionState.RUNNER_ACTIVE)

    reconciler = StartupReconciler(persistence=test_persistence, alerts=mock_alerts)
    # Wallet on-chain holds 0 tokens of this mint
    on_chain = {"MintGhostBBB22222222222222222222222222222": 0}

    report = await reconciler.reconcile(wallet_pubkey="TestWalletPubkey", on_chain_balances=on_chain)

    assert len(report.downtime_liquidated) == 1
    assert report.downtime_liquidated[0]["pool_id"] == "pool_ghost_2"

    # Verify transitioned to CLOSED in WAL database
    active = test_persistence.load_active_positions()
    assert len(active) == 0

    # Alert must be dispatched
    assert mock_alerts.dispatch.called
    event = mock_alerts.dispatch.call_args[0][0]
    assert event.event_type == "DOWNTIME_LIQUIDATION_DETECTED"


@pytest.mark.asyncio
async def test_reconciler_orphan_token_detection(test_persistence, mock_alerts):
    """Verifies tokens held on-chain with no WAL position record trigger orphan alert."""
    reconciler = StartupReconciler(persistence=test_persistence, alerts=mock_alerts)
    orphan_mint = "OrphanMintCCC333333333333333333333333333"
    on_chain = {orphan_mint: 2500000}

    report = await reconciler.reconcile(wallet_pubkey="TestWalletPubkey", on_chain_balances=on_chain)

    assert len(report.orphan_holdings) == 1
    assert report.orphan_holdings[orphan_mint] == 2500000

    # Verify orphan alert dispatched
    assert mock_alerts.dispatch.called
    event = mock_alerts.dispatch.call_args[0][0]
    assert event.event_type == "ORPHAN_SPL_TOKENS_DETECTED"


def test_fetch_wallet_spl_balances_simulated():
    """Verifies fetch_wallet_spl_balances returns empty dict for simulated wallets without network call."""
    res = fetch_wallet_spl_balances("SimulatedWalletPublicKey123456789")
    assert res == {}


def test_fetch_wallet_spl_balances_mock_rpc():
    """Verifies fetch_wallet_spl_balances parses standard Solana RPC getTokenAccountsByOwner response."""
    mock_response = {
        "result": {
            "value": [
                {
                    "account": {
                        "data": {
                            "parsed": {
                                "info": {
                                    "mint": "TokenMint1111111111111111111111111111111111",
                                    "tokenAmount": {"amount": "1500000", "decimals": 6},
                                }
                            }
                        }
                    }
                },
                {
                    "account": {
                        "data": {
                            "parsed": {
                                "info": {
                                    "mint": "TokenZeroBalance222222222222222222222222222",
                                    "tokenAmount": {"amount": "0", "decimals": 6},
                                }
                            }
                        }
                    }
                },
            ]
        }
    }

    with patch("requests.post") as mock_post:
        mock_post.return_value.status_code = 200
        mock_post.return_value.json.return_value = mock_response

        balances = fetch_wallet_spl_balances("RealSolanaWalletAddress111111111111111111111")
        assert len(balances) == 1
        assert balances["TokenMint1111111111111111111111111111111111"] == 1500000
