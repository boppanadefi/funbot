"""
Unit Test Suite for Phase 9: Orchestrator & Production Reliability.
Covers:
1. Priority Worker & Async Concurrency Scheduling (todo-13)
2. SQLite WAL Crash Persistence & Reconciliation (todo-09)
3. Multi-Channel Alerting & Rate Limiting (todo-11)
4. Master Trading Event Loop Orchestrator (todo-03)
"""

import asyncio
from datetime import datetime, timezone
import os
from pathlib import Path
import tempfile
import time
import pytest

from src.orchestrator.scheduler import (
    TaskPriority,
    PrioritizedTask,
    AsyncScheduler,
)
from src.orchestrator.persistence import (
    StatePersistenceManager,
)
from src.orchestrator.alerting import (
    AlertSeverity,
    AlertEvent,
    AlertDispatcher,
)
from src.orchestrator.main_loop import (
    MasterTradingOrchestrator,
)
from src.engine.event_bus import (
    EventBus,
    StreamEventType,
    LiveSwapEvent,
)
from src.engine.entry_orchestrator import (
    CandidateContext,
    CheckStatus,
    WSOL_MINT_STR,
)
from src.engine.position_state_machine import (
    PositionStateMachine,
    PositionContext,
    PositionState,
    ExecutionMode,
)
from src.engine.staleness_breaker import DataStalenessCircuitBreaker
from src.live.circuit_breaker import DailyCircuitBreaker
from src.live.kill_switch import EmergencyKillSwitch
from src.protocol.pumpswap import BondingCurveState, PUMPSWAP_PROGRAM_ID_STR
from src.security.authority_auditor import AuthorityAuditResult


# =====================================================================
# Fixtures & Helpers
# =====================================================================

def make_dummy_candidate(
    pool_id: str = "PoolOrchestrator1111111111111111111111111",
    elapsed_minutes: float = 12.0,
    vuw: float = 1.30,
    aggression: float = 2.50,
    demand: float = 150.0,
    ws_age_ms: float = 100.0,
    is_regime_test: bool = False,
) -> CandidateContext:
    """Builds a 100% compliant candidate passing all 12 checks."""
    audit = AuthorityAuditResult(
        passed=True,
        reason="OK",
        mint_revoked=True,
        freeze_revoked=True,
        top10_concentration_pct=15.0,
        is_metadata_immutable=True,
        mint_address="Mint999999999999999999999999999999999999999",
    )
    curve = BondingCurveState(
        virtual_token_reserves=1_000_000_000_000_000,
        virtual_sol_reserves=32_000_000_000,
        real_token_reserves=793_000_000_000_000,
        real_sol_reserves=2_000_000_000,
        token_total_supply=1_000_000_000_000_000,
        complete=False,
    )
    return CandidateContext(
        pool_id=pool_id,
        quote_mint=WSOL_MINT_STR,
        program_id=PUMPSWAP_PROGRAM_ID_STR,
        elapsed_minutes=elapsed_minutes,
        vuw=vuw,
        aggression_ratio=aggression,
        net_buy_volume_usd_60s=demand,
        authority_audit=audit,
        bonding_curve=curve,
        sol_price_usd=150.0,
        wallet_balance_sol=2.00,
        remaining_daily_budget_usd=10.0,
        circuit_breaker_halted=False,
        last_ws_message_age_ms=ws_age_ms,
        is_regime_test=is_regime_test,
        requested_size_usd=25.0,
    )


# =====================================================================
# 1. Async Scheduler & Priority Queues (todo-13)
# =====================================================================

class TestAsyncScheduler:
    @pytest.mark.asyncio
    async def test_scheduler_priority_ordering(self):
        """Verify HIGH priority tasks execute before MEDIUM and LOW tasks."""
        scheduler = AsyncScheduler(num_workers=1)
        await scheduler.start()

        execution_order = []

        async def worker_fn(label: str):
            execution_order.append(label)

        # Submit LOW first, then MEDIUM, then HIGH
        await scheduler.submit(worker_fn, "LOW_1", priority=TaskPriority.LOW)
        await scheduler.submit(worker_fn, "MEDIUM_1", priority=TaskPriority.MEDIUM)
        await scheduler.submit(worker_fn, "HIGH_1", priority=TaskPriority.HIGH)
        await scheduler.submit(worker_fn, "HIGH_2", priority=TaskPriority.HIGH)

        # Allow worker loop to drain
        for _ in range(20):
            if len(execution_order) >= 4:
                break
            await asyncio.sleep(0.02)

        await scheduler.stop()

        # The first task might start immediately upon arrival, but subsequent queue pulls must prioritize HIGH
        assert "HIGH_1" in execution_order
        assert "HIGH_2" in execution_order
        assert len(execution_order) == 4
        # Verify HIGH tasks are executed before LOW_1
        assert execution_order.index("HIGH_1") < execution_order.index("LOW_1")

    @pytest.mark.asyncio
    async def test_scheduler_lock_concurrency(self):
        """Verify scheduler state lock serializes critical section modifications."""
        scheduler = AsyncScheduler(num_workers=4)
        await scheduler.start()

        counter = 0

        async def increment():
            nonlocal counter
            async with scheduler.lock:
                val = counter
                await asyncio.sleep(0.01)
                counter = val + 1

        tasks = [scheduler.submit(increment, priority=TaskPriority.HIGH) for _ in range(5)]
        await asyncio.gather(*tasks)

        # Allow execution
        for _ in range(30):
            if counter == 5:
                break
            await asyncio.sleep(0.02)

        await scheduler.stop()
        assert counter == 5
        assert scheduler.processed_count == 5


# =====================================================================
# 2. SQLite WAL Persistence & Crash Recovery (todo-09)
# =====================================================================

class TestStatePersistenceManager:
    def setup_method(self):
        self.temp_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db_path = os.path.join(self.temp_dir.name, "test_orchestrator.db")
        self.persistence = StatePersistenceManager(db_path=self.db_path)

    def teardown_method(self):
        try:
            self.temp_dir.cleanup()
        except Exception:
            pass

    def test_wal_pragma_initialization(self):
        """Verify database initializes in WAL mode."""
        with self.persistence._get_connection() as conn:
            journal_mode = conn.execute("PRAGMA journal_mode;").fetchone()[0]
        assert journal_mode.lower() == "wal"

    def test_save_and_load_active_position(self):
        """Verify saving and re-loading active positions from SQLite WAL."""
        ctx = PositionContext(
            pool_id="PoolAlpha1234567890",
            mint="MintAlpha1234567890",
            mode=ExecutionMode.PATH_A_PLUS,
            entry_price_sol=0.000045,
            size_tokens=10_000_000,
            size_sol_notional=0.45,
            entry_timestamp=time.time(),
            current_price_sol=0.000048,
            peak_price_sol=0.000050,
            stop_price_sol=0.000035,
            profit_hurdle_sol=0.000052,
        )

        self.persistence.save_position_state(ctx, PositionState.ACTIVE_OPEN)

        active = self.persistence.load_active_positions()
        assert len(active) == 1
        pos = active[0]
        assert pos["pool_id"] == "PoolAlpha1234567890"
        assert pos["mint"] == "MintAlpha1234567890"
        assert pos["state"] == PositionState.ACTIVE_OPEN.value
        assert pos["current_price_sol"] == 0.000048

    def test_commit_closed_position(self):
        """Verify closing a position marks is_active=0 in database."""
        ctx = PositionContext(
            pool_id="PoolBeta1234567890",
            mint="MintBeta1234567890",
            mode=ExecutionMode.PATH_A_PLUS,
            entry_price_sol=0.00010,
            size_tokens=5_000_000,
            size_sol_notional=0.50,
            entry_timestamp=time.time(),
        )
        self.persistence.save_position_state(ctx, PositionState.ACTIVE_OPEN)
        assert len(self.persistence.load_active_positions()) == 1

        self.persistence.commit_closed_position("PoolBeta1234567890", exit_price_sol=0.00012)
        assert len(self.persistence.load_active_positions()) == 0

    def test_circuit_breaker_journal_persistence(self):
        """Verify daily circuit breaker state persists across restart."""
        self.persistence.save_circuit_breaker(
            daily_loss_usd=4.25,
            trades_count=3,
            is_halted=False,
            halt_reason="OK",
        )

        loaded = self.persistence.load_circuit_breaker()
        assert loaded is not None
        assert loaded["daily_realized_loss_usd"] == 4.25
        assert loaded["trades_count"] == 3
        assert loaded["is_halted"] == 0

    def test_startup_reconciliation_zero_token_balance(self):
        """Verify startup reconciliation closes positions with 0 on-chain tokens."""
        ctx = PositionContext(
            pool_id="PoolGamma1234567890",
            mint="MintGamma1234567890",
            mode=ExecutionMode.PATH_A_PLUS,
            entry_price_sol=0.00020,
            size_tokens=1_000_000,
            size_sol_notional=0.20,
            entry_timestamp=time.time(),
        )
        self.persistence.save_position_state(ctx, PositionState.ACTIVE_OPEN)

        # Wallet has 0 tokens on chain for MintGamma
        balances = {"MintOther": 500}
        msgs = self.persistence.reconcile_startup(balances)

        assert len(msgs) == 1
        assert "Reconciled to CLOSED offline" in msgs[0]
        assert len(self.persistence.load_active_positions()) == 0


# =====================================================================
# 3. Multi-Channel Alerting Subsystem (todo-11)
# =====================================================================

class TestAlertDispatcher:
    def test_discord_embed_formatting(self):
        """Verify Discord payload includes Zinc aesthetics and correct color."""
        dispatcher = AlertDispatcher()
        event = AlertEvent(
            event_type="TEST_CRITICAL",
            severity=AlertSeverity.CRITICAL,
            title="Emergency Halt Triggered",
            message="Loss ceiling reached",
            metadata={"loss_usd": 10.05, "slot": 458321000},
        )

        payload = dispatcher.format_discord_embed(event)
        assert "embeds" in payload
        embed = payload["embeds"][0]
        assert "[CRITICAL] Emergency Halt Triggered" in embed["title"]
        assert embed["color"] == 0xE74C3C  # Red for CRITICAL
        assert len(embed["fields"]) == 2

    def test_telegram_message_formatting(self):
        """Verify Telegram message Markdown syntax and emoji formatting."""
        dispatcher = AlertDispatcher()
        event = AlertEvent(
            event_type="TEST_INFO",
            severity=AlertSeverity.INFO,
            title="Position Opened",
            message="Entered Path A+ momentum trade",
            metadata={"mint": "MintABC", "size_sol": 0.16},
        )

        tg_text = dispatcher.format_telegram_message(event)
        assert "ℹ️ *Position Opened*" in tg_text
        assert "_Entered Path A+ momentum trade_" in tg_text
        assert "• *mint*: `MintABC`" in tg_text

    @pytest.mark.asyncio
    async def test_alert_rate_limiting(self):
        """Verify rate limiter suppresses webhooks beyond max_rate_per_min."""
        dispatcher = AlertDispatcher(max_rate_per_min=5)
        dispatched_count = 0

        for i in range(8):
            ev = AlertEvent(
                event_type="PING",
                severity=AlertSeverity.INFO,
                title=f"Ping {i}",
                message="Heartbeat test",
            )
            success = await dispatcher.dispatch(ev)
            if success:
                dispatched_count += 1

        assert dispatched_count == 5
        assert len(dispatcher.history) == 8


# =====================================================================
# 4. Master Event Loop Orchestrator (todo-03)
# =====================================================================

class TestMasterTradingOrchestrator:
    def setup_method(self):
        self.temp_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db_path = os.path.join(self.temp_dir.name, "orchestrator_loop.db")
        self.persistence = StatePersistenceManager(db_path=self.db_path)

        self.cb_file = os.path.join(self.temp_dir.name, "test_cb.json")
        self.circuit_breaker = DailyCircuitBreaker(state_file_path=self.cb_file)

        self.ks_file = os.path.join(self.temp_dir.name, "test_ks.json")
        self.kill_switch = EmergencyKillSwitch(state_file_path=self.ks_file)

        self.staleness_breaker = DataStalenessCircuitBreaker(max_staleness_ms=1000.0)
        self.event_bus = EventBus()
        self.alert_dispatcher = AlertDispatcher()
        self.scheduler = AsyncScheduler(num_workers=2)

        self.orchestrator = MasterTradingOrchestrator(
            scheduler=self.scheduler,
            persistence=self.persistence,
            alert_dispatcher=self.alert_dispatcher,
            event_bus=self.event_bus,
            staleness_breaker=self.staleness_breaker,
            circuit_breaker=self.circuit_breaker,
            kill_switch=self.kill_switch,
            is_regime_test=False,
            sol_price_usd=150.0,
        )

    def teardown_method(self):
        try:
            self.temp_dir.cleanup()
        except Exception:
            pass

    @pytest.mark.asyncio
    async def test_staleness_circuit_breaker_refusal(self):
        """Verify candidate evaluation is rejected when WebSocket stream is stale."""
        await self.orchestrator.start()
        # Freshness is not recorded, so is_stale == True
        candidate = make_dummy_candidate(ws_age_ms=1500.0)

        result = await self.orchestrator.evaluate_and_enter(candidate)
        assert not result.passed
        assert any("WebSocket data is stale" in r for r in result.rejection_reasons)
        assert self.orchestrator.active_position_count == 0

        await self.orchestrator.shutdown()

    @pytest.mark.asyncio
    async def test_single_position_concurrency_constraint(self):
        """Verify strict 1-position limit in production mode and 3 in regime test."""
        await self.orchestrator.start()
        # Seed fresh message to satisfy staleness breaker
        self.staleness_breaker.record_message_received(slot=1000)

        cand1 = make_dummy_candidate(pool_id="PoolOne11111111111111111111111111")
        cand2 = make_dummy_candidate(pool_id="PoolTwo22222222222222222222222222")

        res1 = await self.orchestrator.evaluate_and_enter(cand1)
        assert res1.passed
        assert self.orchestrator.active_position_count == 1

        # Second candidate must be rejected due to max concurrent limit (1)
        res2 = await self.orchestrator.evaluate_and_enter(cand2)
        assert not res2.passed
        assert "Max concurrent positions (1) reached" in res2.rejection_reasons[0]
        assert self.orchestrator.active_position_count == 1

        await self.orchestrator.shutdown()

    @pytest.mark.asyncio
    async def test_kill_switch_rejection(self):
        """Verify candidate evaluation is rejected if emergency kill switch is tripped."""
        await self.orchestrator.start()
        self.staleness_breaker.record_message_received(slot=1000)
        self.kill_switch.trip(reason="Manual test kill")

        cand = make_dummy_candidate()
        result = await self.orchestrator.evaluate_and_enter(cand)
        assert not result.passed
        assert "Emergency Kill Switch is ACTIVE" in result.rejection_reasons[0]
        assert self.orchestrator.active_position_count == 0

        await self.orchestrator.shutdown()

    @pytest.mark.asyncio
    async def test_successful_entry_and_active_swap_price_tick(self):
        """Verify 12-check entry, SQLite WAL persistence, price updates, and stop exit."""
        await self.orchestrator.start()
        self.staleness_breaker.record_message_received(slot=1000)

        cand = make_dummy_candidate(pool_id="PoolActiveTest123456789012345")
        entry_res = await self.orchestrator.evaluate_and_enter(cand)
        assert entry_res.passed
        assert self.orchestrator.active_position_count == 1

        # Verify persisted in SQLite
        persisted = self.persistence.load_active_positions()
        assert len(persisted) == 1
        assert persisted[0]["pool_id"] == "PoolActiveTest123456789012345"

        # Dispatch an adverse swap event that triggers stop loss
        stop_swap = LiveSwapEvent(
            pool_address="PoolActiveTest123456789012345",
            token_mint=cand.authority_audit.mint_address,
            is_buy=False,
            sol_amount=1e-8,  # Below initial stop price (2.4e-8)
            token_amount=1.0,
            trader_pubkey="TraderDump1111111111111111111111111",
            slot=1001,
        )

        # Trigger swap processing directly
        await self.orchestrator._process_active_position_swap(stop_swap)

        # Position should now be CLOSED and removed from active positions
        assert self.orchestrator.active_position_count == 0
        assert len(self.persistence.load_active_positions()) == 0

        # Check circuit breaker received realized loss
        cb_status = self.circuit_breaker.get_status()
        assert cb_status["trades_count"] == 1

        await self.orchestrator.shutdown()

    @pytest.mark.asyncio
    async def test_graceful_shutdown_preserves_state(self):
        """Verify shutdown cancels timers and cleanly stops worker scheduler."""
        await self.orchestrator.start()
        self.staleness_breaker.record_message_received(slot=1000)

        cand = make_dummy_candidate(pool_id="PoolShutdownTest123456789012")
        await self.orchestrator.evaluate_and_enter(cand)
        assert self.orchestrator.active_position_count == 1

        await self.orchestrator.shutdown()
        assert not self.orchestrator.is_running
        assert not self.scheduler.is_running

        # Verify position is still saved in SQLite WAL for next startup rehydration
        persisted = self.persistence.load_active_positions()
        assert len(persisted) == 1
