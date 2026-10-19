"""
Live Trade Ledger & Execution Journal Subsystem.
Maintains high-fidelity accounting and SQLite WAL persistence for real-world
on-chain micro-trading executions on Solana Mainnet.
"""

from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
import json
import logging
import os
from pathlib import Path
import sqlite3
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger("LiveLedger")

DEFAULT_DB_PATH = "data/telemetry/orchestrator_state.db"


@dataclass
class LiveTradeRecord:
    """Detailed transactional record of a live micro-capital trade."""

    position_id: str
    pool_address: str
    token_mint: str
    regime: str
    entry_signature: str
    entry_timestamp: float
    exit_signature: Optional[str] = None
    exit_timestamp: Optional[float] = None
    duration_minutes: float = 0.0
    quoted_entry_price_sol: float = 0.0
    actual_entry_fill_price_sol: float = 0.0
    entry_slippage_pct: float = 0.0
    quoted_exit_price_sol: float = 0.0
    actual_exit_fill_price_sol: float = 0.0
    exit_slippage_pct: float = 0.0
    total_slippage_pct: float = 0.0
    size_tokens: int = 0
    sol_spent: float = 0.0
    sol_received: float = 0.0
    actual_venue_fee_sol: float = 0.0
    actual_priority_fee_sol: float = 0.0
    actual_jito_tip_sol: float = 0.0
    net_pnl_sol: float = 0.0
    net_pnl_usd: float = 0.0
    net_return_pct: float = 0.0
    exit_reason: str = "ACTIVE"
    ratchet_activated: bool = False
    mev_sandwich_detected: bool = False
    status: str = "OPEN"  # "OPEN", "CLOSED", "CANCELLED"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class LiveLedgerManager:
    """
    Manages persistent SQLite WAL storage of live on-chain trades,
    slippage measurements, and fee accounting.
    """

    def __init__(self, db_path: str = DEFAULT_DB_PATH, sol_price_usd: float = 155.0):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.sol_price_usd = sol_price_usd
        self._init_db()

    @contextmanager
    def _get_connection(self):
        conn = sqlite3.connect(str(self.db_path), timeout=15.0)
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
                CREATE TABLE IF NOT EXISTS live_trades (
                    position_id TEXT PRIMARY KEY,
                    pool_address TEXT NOT NULL,
                    token_mint TEXT NOT NULL,
                    regime TEXT NOT NULL,
                    entry_signature TEXT NOT NULL,
                    entry_timestamp REAL NOT NULL,
                    exit_signature TEXT,
                    exit_timestamp REAL,
                    duration_minutes REAL NOT NULL DEFAULT 0.0,
                    quoted_entry_price_sol REAL NOT NULL DEFAULT 0.0,
                    actual_entry_fill_price_sol REAL NOT NULL DEFAULT 0.0,
                    entry_slippage_pct REAL NOT NULL DEFAULT 0.0,
                    quoted_exit_price_sol REAL NOT NULL DEFAULT 0.0,
                    actual_exit_fill_price_sol REAL NOT NULL DEFAULT 0.0,
                    exit_slippage_pct REAL NOT NULL DEFAULT 0.0,
                    total_slippage_pct REAL NOT NULL DEFAULT 0.0,
                    size_tokens INTEGER NOT NULL DEFAULT 0,
                    sol_spent REAL NOT NULL DEFAULT 0.0,
                    sol_received REAL NOT NULL DEFAULT 0.0,
                    actual_venue_fee_sol REAL NOT NULL DEFAULT 0.0,
                    actual_priority_fee_sol REAL NOT NULL DEFAULT 0.0,
                    actual_jito_tip_sol REAL NOT NULL DEFAULT 0.0,
                    net_pnl_sol REAL NOT NULL DEFAULT 0.0,
                    net_pnl_usd REAL NOT NULL DEFAULT 0.0,
                    net_return_pct REAL NOT NULL DEFAULT 0.0,
                    exit_reason TEXT NOT NULL DEFAULT 'ACTIVE',
                    ratchet_activated INTEGER NOT NULL DEFAULT 0,
                    mev_sandwich_detected INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'OPEN'
                );

                CREATE INDEX IF NOT EXISTS idx_live_trades_mint ON live_trades(token_mint);
                CREATE INDEX IF NOT EXISTS idx_live_trades_entry_ts ON live_trades(entry_timestamp);
                CREATE INDEX IF NOT EXISTS idx_live_trades_status ON live_trades(status);
            """)

    def record_entry(
        self,
        position_id: str,
        pool_address: str,
        token_mint: str,
        regime: str,
        entry_signature: str,
        sol_spent: float,
        size_tokens: int,
        quoted_price_sol: float,
        actual_fill_price_sol: float,
        venue_fee_sol: float = 0.0,
        priority_fee_sol: float = 0.0,
        jito_tip_sol: float = 0.0,
        entry_timestamp: Optional[float] = None,
    ) -> LiveTradeRecord:
        """Records an opened live trade entry with quoted vs actual execution price."""
        now = entry_timestamp or time.time()
        slippage_pct = 0.0
        if quoted_price_sol > 0:
            slippage_pct = ((actual_fill_price_sol - quoted_price_sol) / quoted_price_sol) * 100.0

        record = LiveTradeRecord(
            position_id=position_id,
            pool_address=pool_address,
            token_mint=token_mint,
            regime=regime,
            entry_signature=entry_signature,
            entry_timestamp=now,
            quoted_entry_price_sol=quoted_price_sol,
            actual_entry_fill_price_sol=actual_fill_price_sol,
            entry_slippage_pct=round(slippage_pct, 4),
            size_tokens=size_tokens,
            sol_spent=sol_spent,
            actual_venue_fee_sol=venue_fee_sol,
            actual_priority_fee_sol=priority_fee_sol,
            actual_jito_tip_sol=jito_tip_sol,
            status="OPEN",
        )

        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO live_trades (
                    position_id, pool_address, token_mint, regime, entry_signature,
                    entry_timestamp, quoted_entry_price_sol, actual_entry_fill_price_sol,
                    entry_slippage_pct, size_tokens, sol_spent, actual_venue_fee_sol,
                    actual_priority_fee_sol, actual_jito_tip_sol, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(position_id) DO UPDATE SET
                    quoted_entry_price_sol=excluded.quoted_entry_price_sol,
                    actual_entry_fill_price_sol=excluded.actual_entry_fill_price_sol,
                    entry_slippage_pct=excluded.entry_slippage_pct,
                    sol_spent=excluded.sol_spent,
                    size_tokens=excluded.size_tokens;
                """,
                (
                    record.position_id,
                    record.pool_address,
                    record.token_mint,
                    record.regime,
                    record.entry_signature,
                    record.entry_timestamp,
                    record.quoted_entry_price_sol,
                    record.actual_entry_fill_price_sol,
                    record.entry_slippage_pct,
                    record.size_tokens,
                    record.sol_spent,
                    record.actual_venue_fee_sol,
                    record.actual_priority_fee_sol,
                    record.actual_jito_tip_sol,
                    record.status,
                ),
            )

        logger.info(f"Recorded live entry: {position_id} ({token_mint[:8]}...), spent={sol_spent:.5f} SOL")
        return record

    def record_exit(
        self,
        position_id: str,
        exit_signature: str,
        sol_received: float,
        quoted_price_sol: float,
        actual_fill_price_sol: float,
        exit_reason: str,
        venue_fee_sol: float = 0.0,
        priority_fee_sol: float = 0.0,
        jito_tip_sol: float = 0.0,
        ratchet_activated: bool = False,
        mev_sandwich_detected: bool = False,
        exit_timestamp: Optional[float] = None,
    ) -> Optional[LiveTradeRecord]:
        """Finalizes a closed live trade with realized cash flow and net performance."""
        now = exit_timestamp or time.time()
        existing = self.get_trade(position_id)
        if not existing:
            logger.error(f"Cannot record exit: position {position_id} not found in ledger.")
            return None

        # Calculate duration
        duration_min = max(0.0, (now - existing.entry_timestamp) / 60.0)

        # Exit slippage: for sells, getting less than quoted is adverse slippage
        exit_slippage_pct = 0.0
        if quoted_price_sol > 0:
            exit_slippage_pct = ((quoted_price_sol - actual_fill_price_sol) / quoted_price_sol) * 100.0
        total_slippage_pct = existing.entry_slippage_pct + exit_slippage_pct

        total_venue_fee = existing.actual_venue_fee_sol + venue_fee_sol
        total_priority_fee = existing.actual_priority_fee_sol + priority_fee_sol
        total_jito_tip = existing.actual_jito_tip_sol + jito_tip_sol
        total_fees = total_venue_fee + total_priority_fee + total_jito_tip

        # Net cash flow PnL
        net_pnl_sol = (sol_received - existing.sol_spent) - (priority_fee_sol + jito_tip_sol)
        net_pnl_usd = net_pnl_sol * self.sol_price_usd
        net_return_pct = (net_pnl_sol / existing.sol_spent * 100.0) if existing.sol_spent > 0 else 0.0

        with self._get_connection() as conn:
            conn.execute(
                """
                UPDATE live_trades SET
                    exit_signature = ?,
                    exit_timestamp = ?,
                    duration_minutes = ?,
                    quoted_exit_price_sol = ?,
                    actual_exit_fill_price_sol = ?,
                    exit_slippage_pct = ?,
                    total_slippage_pct = ?,
                    sol_received = ?,
                    actual_venue_fee_sol = ?,
                    actual_priority_fee_sol = ?,
                    actual_jito_tip_sol = ?,
                    net_pnl_sol = ?,
                    net_pnl_usd = ?,
                    net_return_pct = ?,
                    exit_reason = ?,
                    ratchet_activated = ?,
                    mev_sandwich_detected = ?,
                    status = 'CLOSED'
                WHERE position_id = ?
                """,
                (
                    exit_signature,
                    now,
                    round(duration_min, 2),
                    quoted_price_sol,
                    actual_fill_price_sol,
                    round(exit_slippage_pct, 4),
                    round(total_slippage_pct, 4),
                    sol_received,
                    total_venue_fee,
                    total_priority_fee,
                    total_jito_tip,
                    round(net_pnl_sol, 6),
                    round(net_pnl_usd, 2),
                    round(net_return_pct, 2),
                    exit_reason,
                    1 if ratchet_activated else 0,
                    1 if mev_sandwich_detected else 0,
                    position_id,
                ),
            )

        logger.info(
            f"Recorded live exit: {position_id}, net={net_pnl_sol:+.5f} SOL (${net_pnl_usd:+.2f}), "
            f"return={net_return_pct:+.2f}%, reason={exit_reason}"
        )
        return self.get_trade(position_id)

    def get_trade(self, position_id: str) -> Optional[LiveTradeRecord]:
        """Retrieves single live trade by position ID."""
        with self._get_connection() as conn:
            row = conn.execute("SELECT * FROM live_trades WHERE position_id = ?", (position_id,)).fetchone()
            if row:
                return self._row_to_record(row)
        return None

    def get_recent_live_trades(self, limit: int = 10, status: Optional[str] = None) -> List[LiveTradeRecord]:
        """Fetches latest live trades ordered by entry timestamp descending."""
        with self._get_connection() as conn:
            if status:
                cursor = conn.execute(
                    "SELECT * FROM live_trades WHERE status = ? ORDER BY entry_timestamp DESC LIMIT ?",
                    (status, limit),
                )
            else:
                cursor = conn.execute(
                    "SELECT * FROM live_trades ORDER BY entry_timestamp DESC LIMIT ?",
                    (limit,),
                )
            return [self._row_to_record(row) for row in cursor.fetchall()]

    def load_open_trades(self) -> List[LiveTradeRecord]:
        """Fetches all open active live trades."""
        return self.get_recent_live_trades(limit=100, status="OPEN")

    def get_aggregate_metrics(self) -> Dict[str, Any]:
        """Calculates portfolio-level performance aggregates across closed live trades."""
        with self._get_connection() as conn:
            cursor = conn.execute("SELECT * FROM live_trades WHERE status = 'CLOSED'")
            closed_trades = [self._row_to_record(row) for row in cursor.fetchall()]

        if not closed_trades:
            return {
                "total_trades": 0,
                "winning_trades": 0,
                "losing_trades": 0,
                "win_rate_pct": 0.0,
                "total_net_pnl_sol": 0.0,
                "total_net_pnl_usd": 0.0,
                "avg_return_pct": 0.0,
                "avg_slippage_pct": 0.0,
                "total_venue_fees_sol": 0.0,
                "total_priority_fees_sol": 0.0,
                "total_jito_tips_sol": 0.0,
                "total_friction_sol": 0.0,
                "sandwiches_detected": 0,
            }

        total = len(closed_trades)
        winners = [t for t in closed_trades if t.net_pnl_sol > 0]
        losers = [t for t in closed_trades if t.net_pnl_sol <= 0]
        sandwiches = sum(1 for t in closed_trades if t.mev_sandwich_detected)

        total_net_pnl_sol = sum(t.net_pnl_sol for t in closed_trades)
        total_net_pnl_usd = sum(t.net_pnl_usd for t in closed_trades)
        avg_ret = sum(t.net_return_pct for t in closed_trades) / total
        avg_slip = sum(t.total_slippage_pct for t in closed_trades) / total

        venue_fees = sum(t.actual_venue_fee_sol for t in closed_trades)
        priority_fees = sum(t.actual_priority_fee_sol for t in closed_trades)
        jito_tips = sum(t.actual_jito_tip_sol for t in closed_trades)

        return {
            "total_trades": total,
            "winning_trades": len(winners),
            "losing_trades": len(losers),
            "win_rate_pct": round((len(winners) / total) * 100.0, 2),
            "total_net_pnl_sol": round(total_net_pnl_sol, 6),
            "total_net_pnl_usd": round(total_net_pnl_usd, 2),
            "avg_return_pct": round(avg_ret, 2),
            "avg_slippage_pct": round(avg_slip, 4),
            "total_venue_fees_sol": round(venue_fees, 6),
            "total_priority_fees_sol": round(priority_fees, 6),
            "total_jito_tips_sol": round(jito_tips, 6),
            "total_friction_sol": round(venue_fees + priority_fees + jito_tips, 6),
            "sandwiches_detected": sandwiches,
        }

    def _row_to_record(self, row: sqlite3.Row) -> LiveTradeRecord:
        d = dict(row)
        return LiveTradeRecord(
            position_id=d["position_id"],
            pool_address=d["pool_address"],
            token_mint=d["token_mint"],
            regime=d["regime"],
            entry_signature=d["entry_signature"],
            entry_timestamp=d["entry_timestamp"],
            exit_signature=d.get("exit_signature"),
            exit_timestamp=d.get("exit_timestamp"),
            duration_minutes=d.get("duration_minutes", 0.0),
            quoted_entry_price_sol=d.get("quoted_entry_price_sol", 0.0),
            actual_entry_fill_price_sol=d.get("actual_entry_fill_price_sol", 0.0),
            entry_slippage_pct=d.get("entry_slippage_pct", 0.0),
            quoted_exit_price_sol=d.get("quoted_exit_price_sol", 0.0),
            actual_exit_fill_price_sol=d.get("actual_exit_fill_price_sol", 0.0),
            exit_slippage_pct=d.get("exit_slippage_pct", 0.0),
            total_slippage_pct=d.get("total_slippage_pct", 0.0),
            size_tokens=d.get("size_tokens", 0),
            sol_spent=d.get("sol_spent", 0.0),
            sol_received=d.get("sol_received", 0.0),
            actual_venue_fee_sol=d.get("actual_venue_fee_sol", 0.0),
            actual_priority_fee_sol=d.get("actual_priority_fee_sol", 0.0),
            actual_jito_tip_sol=d.get("actual_jito_tip_sol", 0.0),
            net_pnl_sol=d.get("net_pnl_sol", 0.0),
            net_pnl_usd=d.get("net_pnl_usd", 0.0),
            net_return_pct=d.get("net_return_pct", 0.0),
            exit_reason=d.get("exit_reason", "ACTIVE"),
            ratchet_activated=bool(d.get("ratchet_activated", 0)),
            mev_sandwich_detected=bool(d.get("mev_sandwich_detected", 0)),
            status=d.get("status", "OPEN"),
        )
