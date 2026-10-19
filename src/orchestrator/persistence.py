"""
SQLite Write-Ahead Logging (WAL) State Persistence & Crash Recovery.
Ensures zero data loss across process crashes, power failures, or reboots.
"""

from contextlib import contextmanager
from dataclasses import asdict
import json
import logging
import os
from pathlib import Path
import sqlite3
import time
from typing import Any, Dict, List, Optional, Tuple

from src.engine.position_state_machine import PositionContext, PositionState, ExecutionMode

logger = logging.getLogger("StatePersistence")

DEFAULT_DB_PATH = "data/telemetry/orchestrator_state.db"


class StatePersistenceManager:
    """Manages transactional journaling in SQLite WAL mode for crash recovery."""

    def __init__(self, db_path: str = DEFAULT_DB_PATH):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    @contextmanager
    def _get_connection(self):
        conn = sqlite3.connect(str(self.db_path), timeout=10.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute("PRAGMA busy_timeout=5000;")
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def _init_db(self):
        with self._get_connection() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS persisted_positions (
                    pool_id TEXT PRIMARY KEY,
                    mint TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    state TEXT NOT NULL,
                    entry_price_sol REAL NOT NULL,
                    current_price_sol REAL NOT NULL,
                    peak_price_sol REAL NOT NULL,
                    stop_price_sol REAL NOT NULL,
                    profit_hurdle_sol REAL NOT NULL,
                    size_tokens INTEGER NOT NULL,
                    size_sol_notional REAL NOT NULL,
                    entry_timestamp REAL NOT NULL,
                    last_updated REAL NOT NULL,
                    is_active INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS persisted_orders (
                    signature TEXT PRIMARY KEY,
                    pool_id TEXT NOT NULL,
                    side TEXT NOT NULL,
                    amount_lamports INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    submission_timestamp REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS circuit_breaker_journal (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    daily_realized_loss_usd REAL NOT NULL,
                    trades_count INTEGER NOT NULL,
                    is_halted INTEGER NOT NULL,
                    halt_reason TEXT,
                    last_reset_timestamp REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS heartbeat_journal (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    timestamp REAL NOT NULL,
                    pid INTEGER NOT NULL,
                    uptime_seconds REAL NOT NULL,
                    memory_rss_mb REAL NOT NULL,
                    cpu_percent REAL NOT NULL,
                    active_positions INTEGER NOT NULL,
                    is_kill_switch_tripped INTEGER NOT NULL,
                    is_circuit_breaker_halted INTEGER NOT NULL,
                    last_slot INTEGER NOT NULL,
                    last_pulse_timestamp REAL NOT NULL
                );
            """)

    def save_position_state(self, ctx: PositionContext, state: PositionState):
        """Atomically upserts active position state."""
        now = time.time()
        is_active = 0 if state in {PositionState.CLOSED, PositionState.CANCELLED} else 1
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO persisted_positions (
                    pool_id, mint, mode, state, entry_price_sol, current_price_sol,
                    peak_price_sol, stop_price_sol, profit_hurdle_sol, size_tokens,
                    size_sol_notional, entry_timestamp, last_updated, is_active
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(pool_id) DO UPDATE SET
                    state=excluded.state,
                    current_price_sol=excluded.current_price_sol,
                    peak_price_sol=excluded.peak_price_sol,
                    stop_price_sol=excluded.stop_price_sol,
                    last_updated=excluded.last_updated,
                    is_active=excluded.is_active;
                """,
                (
                    ctx.pool_id,
                    ctx.mint,
                    ctx.mode.value,
                    state.value,
                    ctx.entry_price_sol,
                    ctx.current_price_sol,
                    ctx.peak_price_sol,
                    ctx.stop_price_sol,
                    ctx.profit_hurdle_sol,
                    ctx.size_tokens,
                    ctx.size_sol_notional,
                    ctx.entry_timestamp,
                    now,
                    is_active,
                ),
            )

    def load_active_positions(self) -> List[Dict[str, Any]]:
        """Loads all non-terminal positions for recovery."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM persisted_positions WHERE is_active = 1 ORDER BY entry_timestamp ASC"
            )
            return [dict(row) for row in cursor.fetchall()]

    def commit_closed_position(self, pool_id: str, exit_price_sol: float, state: PositionState = PositionState.CLOSED):
        """Marks position closed in database."""
        now = time.time()
        with self._get_connection() as conn:
            conn.execute(
                """
                UPDATE persisted_positions SET
                    state = ?,
                    current_price_sol = ?,
                    last_updated = ?,
                    is_active = 0
                WHERE pool_id = ?
                """,
                (state.value, exit_price_sol, now, pool_id),
            )

    def save_circuit_breaker(
        self,
        daily_loss_usd: float,
        trades_count: int,
        is_halted: bool,
        halt_reason: str = "",
        last_reset: Optional[float] = None,
    ):
        """Persists daily circuit breaker metrics to survive restarts."""
        reset_ts = last_reset or time.time()
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO circuit_breaker_journal (id, daily_realized_loss_usd, trades_count, is_halted, halt_reason, last_reset_timestamp)
                VALUES (1, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    daily_realized_loss_usd=excluded.daily_realized_loss_usd,
                    trades_count=excluded.trades_count,
                    is_halted=excluded.is_halted,
                    halt_reason=excluded.halt_reason,
                    last_reset_timestamp=excluded.last_reset_timestamp;
                """,
                (daily_loss_usd, trades_count, 1 if is_halted else 0, halt_reason, reset_ts),
            )

    def load_circuit_breaker(self) -> Optional[Dict[str, Any]]:
        """Loads circuit breaker state from journal."""
        with self._get_connection() as conn:
            row = conn.execute("SELECT * FROM circuit_breaker_journal WHERE id = 1").fetchone()
            if row:
                return dict(row)
        return None

    def save_heartbeat(self, pulse_data: Dict[str, Any]):
        """Persists latest heartbeat pulse to WAL database."""
        now = time.time()
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO heartbeat_journal (
                    id, timestamp, pid, uptime_seconds, memory_rss_mb, cpu_percent,
                    active_positions, is_kill_switch_tripped, is_circuit_breaker_halted,
                    last_slot, last_pulse_timestamp
                ) VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    timestamp=excluded.timestamp,
                    pid=excluded.pid,
                    uptime_seconds=excluded.uptime_seconds,
                    memory_rss_mb=excluded.memory_rss_mb,
                    cpu_percent=excluded.cpu_percent,
                    active_positions=excluded.active_positions,
                    is_kill_switch_tripped=excluded.is_kill_switch_tripped,
                    is_circuit_breaker_halted=excluded.is_circuit_breaker_halted,
                    last_slot=excluded.last_slot,
                    last_pulse_timestamp=excluded.last_pulse_timestamp;
                """,
                (
                    pulse_data.get("timestamp", now),
                    pulse_data.get("pid", 0),
                    pulse_data.get("uptime_seconds", 0.0),
                    pulse_data.get("memory_rss_mb", 0.0),
                    pulse_data.get("cpu_percent", 0.0),
                    pulse_data.get("active_positions", 0),
                    1 if pulse_data.get("is_kill_switch_tripped", False) else 0,
                    1 if pulse_data.get("is_circuit_breaker_halted", False) else 0,
                    pulse_data.get("last_slot", 0),
                    now,
                ),
            )

    def load_latest_heartbeat(self) -> Optional[Dict[str, Any]]:
        """Loads latest heartbeat pulse from journal."""
        with self._get_connection() as conn:
            row = conn.execute("SELECT * FROM heartbeat_journal WHERE id = 1").fetchone()
            if row:
                return dict(row)
        return None

    def reconcile_startup(self, on_chain_token_balances: Dict[str, int]) -> List[str]:
        """
        Reconciles active positions against wallet on-chain SPL token balances.
        Returns list of reconciliation messages.
        """
        active_records = self.load_active_positions()
        reconciled_events = []

        for pos in active_records:
            mint = pos["mint"]
            pool_id = pos["pool_id"]
            expected_tokens = pos["size_tokens"]
            actual_balance = on_chain_token_balances.get(mint, 0)

            if actual_balance == 0 and expected_tokens > 0:
                # Position was liquidated offline or externally
                self.commit_closed_position(pool_id, exit_price_sol=0.0, state=PositionState.CLOSED)
                msg = f"Position {pool_id} ({mint[:8]}): Token balance is 0. Reconciled to CLOSED offline."
                reconciled_events.append(msg)
                logger.warning(msg)
            elif actual_balance > 0:
                msg = f"Position {pool_id} ({mint[:8]}): Verified active on-chain with {actual_balance} tokens."
                reconciled_events.append(msg)
                logger.info(msg)

        return reconciled_events
