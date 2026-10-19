"""
Master Trading Orchestrator & Production Reliability Engine.
Coordinates:
- Priority-based AsyncScheduler (High/Med/Low worker pools)
- Event Ingestion from EventBus (Helius / Triton WebSocket feed)
- 12-Check Entry Decision Gate
- Position Lifecycle State Machine & 5 Concurrent Exit Timers
- SQLite WAL Transactional State Persistence
- Multi-Channel Alert Dispatcher (Discord / Telegram)
- Staleness (<1000ms) & Daily Loss (-$10.00) Circuit Breakers & Kill Switch
- Graceful shutdown with state preservation
"""

import asyncio
from datetime import datetime, timezone
import logging
import signal
import time
from typing import Any, Dict, List, Optional, Tuple

from src.engine.event_bus import (
    EventBus,
    StreamEventType,
    LiveSwapEvent,
    LiveAccountChangeEvent,
)
from src.engine.entry_orchestrator import (
    CandidateContext,
    EntryDecisionOrchestrator,
    EntryEvaluationResult,
    CheckStatus,
)
from src.engine.position_state_machine import (
    PositionStateMachine,
    PositionContext,
    PositionState,
    ExecutionMode,
)
from src.engine.exit_timer_manager import ExitTimerManager
from src.engine.staleness_breaker import DataStalenessCircuitBreaker
from src.live.circuit_breaker import DailyCircuitBreaker
from src.live.kill_switch import EmergencyKillSwitch
from src.orchestrator.scheduler import AsyncScheduler, TaskPriority
from src.orchestrator.persistence import StatePersistenceManager
from src.orchestrator.alerting import AlertDispatcher, AlertEvent, AlertSeverity
from src.orchestrator.heartbeat import HeartbeatPublisher
from src.orchestrator.reconciliation import StartupReconciler
from src.telemetry.telegram_operator_bot import TelegramOperatorBot

logger = logging.getLogger("MasterTradingOrchestrator")


class MasterTradingOrchestrator:
    """Primary asynchronous coordinator for Solana live trading."""

    def __init__(
        self,
        scheduler: Optional[AsyncScheduler] = None,
        persistence: Optional[StatePersistenceManager] = None,
        alert_dispatcher: Optional[AlertDispatcher] = None,
        event_bus: Optional[EventBus] = None,
        staleness_breaker: Optional[DataStalenessCircuitBreaker] = None,
        circuit_breaker: Optional[DailyCircuitBreaker] = None,
        kill_switch: Optional[EmergencyKillSwitch] = None,
        telegram_bot: Optional[TelegramOperatorBot] = None,
        is_regime_test: bool = False,
        max_active_positions: Optional[int] = None,
        sol_price_usd: float = 155.0,
    ):
        self.scheduler = scheduler or AsyncScheduler(num_workers=4)
        self.persistence = persistence or StatePersistenceManager()
        self.alerts = alert_dispatcher or AlertDispatcher()
        self.event_bus = event_bus or EventBus()
        self.staleness_breaker = staleness_breaker or DataStalenessCircuitBreaker()
        self.circuit_breaker = circuit_breaker or DailyCircuitBreaker()
        self.kill_switch = kill_switch or EmergencyKillSwitch()
        self.telegram_bot = telegram_bot

        self.is_regime_test = is_regime_test
        # Concurrency override: 3 positions in regime test, strict 1 in production
        self.max_active_positions = (
            max_active_positions
            if max_active_positions is not None
            else (3 if is_regime_test else 1)
        )
        self.sol_price_usd = sol_price_usd

        # State tracking: pool_id -> (fsm, timer_manager)
        self.active_positions: Dict[str, Tuple[PositionStateMachine, ExitTimerManager]] = {}
        self.candidate_cache: Dict[str, CandidateContext] = {}
        self._is_running = False
        self._shutdown_event = asyncio.Event()

        # Heartbeat liveliness publisher
        self.heartbeat_publisher = HeartbeatPublisher(
            persistence_manager=self.persistence,
            active_positions_provider=lambda: self.active_position_count,
            kill_switch_provider=lambda: self.is_kill_switch_active,
            circuit_breaker_provider=lambda: self.is_circuit_breaker_active,
            slot_provider=lambda: getattr(self.staleness_breaker, "last_observed_slot", 0),
        )
        # State reconciler
        self.reconciler = StartupReconciler(persistence=self.persistence, alerts=self.alerts)
        self._heartbeat_task: Optional[asyncio.Task] = None

    @property
    def is_running(self) -> bool:
        return self._is_running

    @property
    def active_position_count(self) -> int:
        return len(self.active_positions)

    @property
    def is_kill_switch_active(self) -> bool:
        return self.kill_switch.is_tripped()

    @property
    def is_circuit_breaker_active(self) -> bool:
        return self.circuit_breaker.get_status().get("is_halted", False)

    @property
    def is_stale(self) -> bool:
        return self.staleness_breaker.is_stale

    async def start(self, on_chain_balances: Optional[Dict[str, int]] = None):
        """Starts workers, reconciles persistent state, and binds event handlers."""
        if self._is_running:
            return

        logger.info("Starting MasterTradingOrchestrator...")
        await self.scheduler.start()
        self._is_running = True

        # Reconcile any existing active positions from SQLite WAL
        await self.reconcile_startup(on_chain_balances or {})

        # Subscribe to EventBus
        self.event_bus.subscribe(StreamEventType.SWAP, self._on_bus_swap_event)
        self.event_bus.subscribe(StreamEventType.ACCOUNT_CHANGE, self._on_bus_account_change_event)

        # Register signal handlers if supported by the event loop
        try:
            loop = asyncio.get_running_loop()
            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.add_signal_handler(sig, lambda: asyncio.create_task(self.shutdown()))
        except (NotImplementedError, AttributeError):
            # Windows ProactorEventLoop does not support add_signal_handler
            pass

        # Start Telegram Operator Bot polling if configured
        if self.telegram_bot:
            asyncio.create_task(self.telegram_bot.start_polling())

        # Start Heartbeat liveliness publisher
        self.heartbeat_publisher.publish_pulse()
        self._heartbeat_task = asyncio.create_task(self.heartbeat_publisher.run_loop())

        await self.alerts.dispatch(
            AlertEvent(
                event_type="ORCHESTRATOR_START",
                severity=AlertSeverity.INFO,
                title="Orchestrator Started",
                message=(
                    f"Master loop active. Regime test: {self.is_regime_test}. "
                    f"Max concurrent positions: {self.max_active_positions}."
                ),
                metadata={
                    "is_regime_test": self.is_regime_test,
                    "max_positions": self.max_active_positions,
                    "active_hydrated": len(self.active_positions),
                },
            )
        )
        logger.info(f"MasterTradingOrchestrator running with {len(self.active_positions)} active positions.")

    async def shutdown(self):
        """Gracefully halts orchestrator, cancels timers, flushes queues, and preserves state."""
        if not self._is_running:
            return

        logger.warning("Initiating graceful shutdown of MasterTradingOrchestrator...")
        self._is_running = False

        # Stop Telegram bot polling
        if self.telegram_bot:
            self.telegram_bot.stop_polling()

        # Stop Heartbeat liveliness publisher
        self.heartbeat_publisher.stop()
        if self._heartbeat_task and not self._heartbeat_task.done():
            self._heartbeat_task.cancel()

        # Cancel all exit timers for active positions
        for pool_id, (fsm, timer_mgr) in list(self.active_positions.items()):
            timer_mgr.cancel_all()
            if fsm.context:
                self.persistence.save_position_state(fsm.context, fsm.current_state)

        # Unsubscribe from EventBus
        self.event_bus.unsubscribe(StreamEventType.SWAP, self._on_bus_swap_event)
        self.event_bus.unsubscribe(StreamEventType.ACCOUNT_CHANGE, self._on_bus_account_change_event)

        # Stop scheduler workers
        await self.scheduler.stop()

        self._shutdown_event.set()

        await self.alerts.dispatch(
            AlertEvent(
                event_type="ORCHESTRATOR_SHUTDOWN",
                severity=AlertSeverity.INFO,
                title="Orchestrator Halted",
                message="Master trading orchestrator gracefully shut down. State persisted.",
                metadata={"persisted_active_count": len(self.active_positions)},
            )
        )
        logger.info("MasterTradingOrchestrator shutdown complete.")

    async def reconcile_startup(self, on_chain_balances: Dict[str, int]) -> List[str]:
        """Reconciles persisted database positions against wallet balances on startup."""
        report = await self.reconciler.reconcile(on_chain_balances=on_chain_balances)
        messages = report.messages

        # Hydrate non-terminal records into active state machine instances
        active_records = self.persistence.load_active_positions()
        for rec in active_records:
            pool_id = rec["pool_id"]
            if pool_id not in self.active_positions:
                mode = ExecutionMode(rec["mode"])
                ctx = PositionContext(
                    pool_id=pool_id,
                    mint=rec["mint"],
                    mode=mode,
                    entry_price_sol=rec["entry_price_sol"],
                    size_tokens=rec["size_tokens"],
                    size_sol_notional=rec["size_sol_notional"],
                    entry_timestamp=rec["entry_timestamp"],
                    current_price_sol=rec["current_price_sol"],
                    peak_price_sol=rec["peak_price_sol"],
                    stop_price_sol=rec["stop_price_sol"],
                    profit_hurdle_sol=rec["profit_hurdle_sol"],
                )
                fsm = PositionStateMachine(ctx)
                persisted_state = PositionState(rec["state"])
                if persisted_state != PositionState.PENDING_ENTRY:
                    # Transition to active state
                    fsm.transition_to(
                        persisted_state,
                        trigger="STARTUP_REHYDRATION",
                        price_sol=rec["current_price_sol"],
                    )

                timer_mgr = ExitTimerManager(
                    position_id=pool_id,
                    on_timeout_callback=self._handle_timer_timeout,
                )
                # Resume lifespan timer for elapsed remaining time
                elapsed_sec = time.time() - rec["entry_timestamp"]
                remaining_sec = max(1.0, 1200.0 - elapsed_sec)
                timer_mgr.start_lifespan_timer(remaining_sec)

                self.active_positions[pool_id] = (fsm, timer_mgr)
                logger.info(f"Rehydrated position {pool_id} ({rec['mint'][:8]}) with {remaining_sec:.1f}s remaining.")

        return messages

    # -------------------------------------------------------------------------
    # Event Handlers
    # -------------------------------------------------------------------------

    def _on_bus_swap_event(self, event: LiveSwapEvent):
        """Synchronous dispatcher called by EventBus; schedules async handler."""
        if not self._is_running:
            return
        # Record arrival to staleness breaker
        self.staleness_breaker.record_message_received(slot=event.slot)

        # Check if this swap belongs to an active position (HIGH priority)
        if event.pool_address in self.active_positions:
            asyncio.create_task(
                self.scheduler.submit(
                    self._process_active_position_swap,
                    event,
                    priority=TaskPriority.HIGH,
                    task_id=f"swap-active-{event.pool_address[:8]}-{event.slot}",
                )
            )
        else:
            # General market flow update (MEDIUM priority)
            asyncio.create_task(
                self.scheduler.submit(
                    self._process_general_swap,
                    event,
                    priority=TaskPriority.MEDIUM,
                    task_id=f"swap-market-{event.pool_address[:8]}-{event.slot}",
                )
            )

    def _on_bus_account_change_event(self, event: LiveAccountChangeEvent):
        """Synchronous dispatcher called by EventBus for reserve balance changes."""
        if not self._is_running:
            return
        self.staleness_breaker.record_message_received(slot=event.slot)

    async def _process_active_position_swap(self, event: LiveSwapEvent):
        """Processes high-priority price tick on an open position."""
        pool_id = event.pool_address
        if pool_id not in self.active_positions:
            return

        async with self.scheduler.lock:
            if pool_id not in self.active_positions:
                return
            fsm, timer_mgr = self.active_positions[pool_id]

            if event.token_amount and event.token_amount > 0:
                current_price_sol = event.sol_amount / event.token_amount
            else:
                current_price_sol = fsm.context.current_price_sol if fsm.context else 0.0

            should_exit, reason = fsm.update_price_tick(current_price_sol)

            # Persist state update
            if fsm.context:
                self.persistence.save_position_state(fsm.context, fsm.current_state)

            if should_exit:
                logger.warning(f"Stop triggered for {pool_id}: {reason}")
                await self._close_position_unlocked(
                    pool_id=pool_id,
                    trigger=reason or "STOP_TRIGGERED",
                    exit_price_sol=current_price_sol,
                )

    async def _process_general_swap(self, event: LiveSwapEvent):
        """Evaluates market swap for potential entry opportunities if capacity exists."""
        if len(self.active_positions) >= self.max_active_positions:
            return

        pool_id = event.pool_address
        if pool_id in self.candidate_cache:
            cand = self.candidate_cache[pool_id]
            # Update latency
            cand.last_ws_message_age_ms = self.staleness_breaker.elapsed_ms
            await self.evaluate_and_enter(cand)

    async def _handle_timer_timeout(self, timer_name: str, pool_id: str):
        """High-priority timeout handler invoked by ExitTimerManager."""
        logger.warning(f"Timeout [{timer_name}] fired for active position [{pool_id}]")
        await self.scheduler.submit(
            self.close_position,
            pool_id,
            f"TIMEOUT_{timer_name}",
            priority=TaskPriority.HIGH,
            task_id=f"timeout-{timer_name}-{pool_id[:8]}",
        )

    # -------------------------------------------------------------------------
    # Decision & Position Execution Gates
    # -------------------------------------------------------------------------

    async def evaluate_and_enter(self, candidate: CandidateContext) -> EntryEvaluationResult:
        """Evaluates candidate pool against the 12-Check Gate and executes entry if 100% pass."""
        async with self.scheduler.lock:
            # 1. Concurrency Capacity Guard
            if len(self.active_positions) >= self.max_active_positions:
                logger.info(
                    f"Entry skipped for {candidate.pool_id[:8]}: Active positions ({len(self.active_positions)}) "
                    f"at max limit ({self.max_active_positions})."
                )
                return EntryEvaluationResult(
                    passed=False,
                    pool_id=candidate.pool_id,
                    timestamp=time.time(),
                    checks={},
                    rejection_reasons=[f"Max concurrent positions ({self.max_active_positions}) reached."],
                    suggested_size_sol=0.0,
                )

            # 2. Kill Switch Guard
            if self.kill_switch.is_tripped():
                return EntryEvaluationResult(
                    passed=False,
                    pool_id=candidate.pool_id,
                    timestamp=time.time(),
                    checks={},
                    rejection_reasons=["Emergency Kill Switch is ACTIVE."],
                    suggested_size_sol=0.0,
                )

            # 3. Daily Circuit Breaker Guard
            cb_status = self.circuit_breaker.get_status()
            if cb_status.get("is_halted", False):
                return EntryEvaluationResult(
                    passed=False,
                    pool_id=candidate.pool_id,
                    timestamp=time.time(),
                    checks={},
                    rejection_reasons=[f"Daily Circuit Breaker halted: {cb_status.get('halt_reason')}."],
                    suggested_size_sol=0.0,
                )

            # 4. Data Staleness Guard (<1000ms latency ceiling)
            if self.staleness_breaker.is_stale:
                return EntryEvaluationResult(
                    passed=False,
                    pool_id=candidate.pool_id,
                    timestamp=time.time(),
                    checks={},
                    rejection_reasons=[
                        f"WebSocket data is stale ({self.staleness_breaker.elapsed_ms:.1f}ms > 1000ms)."
                    ],
                    suggested_size_sol=0.0,
                )

            # 5. Evaluate the 12 Checks
            candidate.last_ws_message_age_ms = self.staleness_breaker.elapsed_ms
            candidate.circuit_breaker_halted = cb_status.get("is_halted", False)
            candidate.remaining_daily_budget_usd = self.circuit_breaker.get_remaining_budget()
            candidate.is_regime_test = self.is_regime_test

            result = EntryDecisionOrchestrator.evaluate(candidate)

            if not result.passed:
                logger.info(
                    f"Candidate {candidate.pool_id[:8]} REJECTED ({result.failed_count} fails): "
                    f"{result.rejection_reasons[:2]}"
                )
                return result

            # 6. Passed 100% — Initialize Position State Machine
            pool_id = candidate.pool_id
            mint = candidate.authority_audit.mint_address
            mode = ExecutionMode.PATH_A_PLUS  # Invariant 2 Path A+
            entry_price_sol = candidate.bonding_curve.spot_price_sol
            size_sol = result.suggested_size_sol
            size_tokens = int(size_sol / max(1e-12, entry_price_sol))

            ctx = PositionContext(
                pool_id=pool_id,
                mint=mint,
                mode=mode,
                entry_price_sol=entry_price_sol,
                size_tokens=size_tokens,
                size_sol_notional=size_sol,
                entry_timestamp=time.time(),
                current_price_sol=entry_price_sol,
                peak_price_sol=entry_price_sol,
                stop_price_sol=entry_price_sol * 0.75,  # -25% initial hard stop
                profit_hurdle_sol=entry_price_sol * 1.15,  # +15% net profit hurdle
            )

            fsm = PositionStateMachine(ctx)
            # Transition IDLE -> PENDING_ENTRY -> ACTIVE_OPEN
            fsm.transition_to(PositionState.ACTIVE_OPEN, trigger="12_CHECKS_PASSED", price_sol=entry_price_sol)

            # 7. Start 5 Concurrent Exit Timers
            timer_mgr = ExitTimerManager(
                position_id=pool_id,
                on_timeout_callback=self._handle_timer_timeout,
            )
            timer_mgr.start_lifespan_timer(duration_sec=1200.0)  # 20 minutes (Guardrail 7)
            timer_mgr.start_tx_confirmation_watchdog(duration_sec=30.0)

            # 8. Commit to SQLite WAL & In-Memory State
            self.persistence.save_position_state(ctx, fsm.current_state)
            self.active_positions[pool_id] = (fsm, timer_mgr)

            # 9. Dispatch Entry Alert
            await self.alerts.dispatch(
                AlertEvent(
                    event_type="ENTRY_EXECUTED",
                    severity=AlertSeverity.INFO,
                    title=f"Position Opened: {mint[:8]}",
                    message=(
                        f"Entered {size_sol:.4f} SOL ({size_tokens} tokens) @ {entry_price_sol:.6f} SOL. "
                        f"Mode: {mode.value}."
                    ),
                    metadata={
                        "pool_id": pool_id,
                        "mint": mint,
                        "size_sol": size_sol,
                        "entry_price": entry_price_sol,
                        "stop_price": ctx.stop_price_sol,
                        "profit_hurdle": ctx.profit_hurdle_sol,
                    },
                )
            )

            logger.info(f"Successfully opened position {pool_id} ({ctx.mint[:8]}). Active: {len(self.active_positions)}")
            return result

    async def close_position(
        self,
        pool_id: str,
        trigger: str,
        exit_price_sol: Optional[float] = None,
    ):
        """Thread-safely closes an active position using the scheduler lock."""
        async with self.scheduler.lock:
            await self._close_position_unlocked(pool_id, trigger, exit_price_sol)

    async def _close_position_unlocked(
        self,
        pool_id: str,
        trigger: str,
        exit_price_sol: Optional[float] = None,
    ):
        """Executes position closure, updates state machine, records PnL, and persists state."""
        if pool_id not in self.active_positions:
            logger.warning(f"Cannot close position {pool_id}: not found in active positions.")
            return

        fsm, timer_mgr = self.active_positions[pool_id]
        timer_mgr.cancel_all()

        ctx = fsm.context
        current_price = exit_price_sol or (ctx.current_price_sol if ctx else 0.0)

        # Transition PENDING_EXIT -> CLOSED
        if fsm.can_transition_to(PositionState.PENDING_EXIT):
            fsm.transition_to(PositionState.PENDING_EXIT, trigger=trigger, price_sol=current_price)

        fsm.transition_to(PositionState.CLOSED, trigger=trigger, price_sol=current_price)

        # Calculate and record PnL in USD
        net_pnl_usd = 0.0
        if ctx:
            pnl_sol = ctx.realized_pnl_sol
            net_pnl_usd = pnl_sol * self.sol_price_usd
            self.circuit_breaker.record_trade_pnl(net_pnl_usd)
            self.persistence.commit_closed_position(
                pool_id=pool_id,
                exit_price_sol=current_price,
                state=PositionState.CLOSED,
            )

        # Remove from active tracking
        del self.active_positions[pool_id]

        severity = AlertSeverity.WARNING if net_pnl_usd < 0 else AlertSeverity.INFO
        await self.alerts.dispatch(
            AlertEvent(
                event_type="POSITION_CLOSED",
                severity=severity,
                title=f"Position Closed: {pool_id[:8]}",
                message=(
                    f"Closed via {trigger} @ {current_price:.6f} SOL. "
                    f"PnL: {ctx.realized_pnl_pct:+.2f}% (${net_pnl_usd:+.2f})."
                ),
                metadata={
                    "pool_id": pool_id,
                    "trigger": trigger,
                    "exit_price": current_price,
                    "pnl_pct": f"{ctx.realized_pnl_pct:.2f}%",
                    "pnl_usd": round(net_pnl_usd, 2),
                },
            )
        )
        logger.info(
            f"Position {pool_id} CLOSED. Trigger: {trigger}. PnL: ${net_pnl_usd:.2f}. "
            f"Remaining active: {len(self.active_positions)}"
        )
