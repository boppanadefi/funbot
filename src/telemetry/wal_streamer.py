"""
SQLite WAL Real-Time Data Streamer.
Reads engine database state safely without locking active database writers or causing concurrency timeouts.
"""

from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import sqlite3
from typing import Any, Dict, List, Optional

logger = logging.getLogger("SqliteWalStreamer")


class SqliteWalStreamer:
    """Safely extracts real-time state from SQLite WAL databases and persistent state files."""

    def __init__(
        self,
        telemetry_db_path: str = "data/telemetry/telemetry.db",
        orchestrator_db_path: str = "data/telemetry/orchestrator_state.db",
        active_pos_path: str = "data/live/active_paper_position.json",
        kill_switch_path: str = "data/live/kill_switch_state.json",
        circuit_breaker_path: str = "data/live/circuit_breaker_state.json",
    ):
        self.telemetry_db = Path(telemetry_db_path)
        self.orchestrator_db = Path(orchestrator_db_path)
        self.active_pos_file = Path(active_pos_path)
        self.kill_switch_file = Path(kill_switch_path)
        self.circuit_breaker_file = Path(circuit_breaker_path)

    def _get_read_only_conn(self, db_path: Path) -> Optional[sqlite3.Connection]:
        """Creates a non-blocking read-only connection with WAL pragma."""
        if not db_path.exists():
            return None
        try:
            conn = sqlite3.connect(
                f"file:{db_path.resolve().as_posix()}?mode=ro",
                uri=True,
                timeout=5.0,
            )
            conn.row_factory = sqlite3.Row
            return conn
        except Exception as e:
            logger.debug("Failed to connect to %s in read-only mode: %s", db_path, e)
            return None

    def get_recent_trade_events(self, limit: int = 15) -> List[Dict[str, Any]]:
        """Queries recent trade executions or position history."""
        # Check telemetry DB first
        conn = self._get_read_only_conn(self.telemetry_db)
        if conn:
            try:
                cur = conn.cursor()
                # Check available tables
                cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
                tables = {r[0] for r in cur.fetchall()}
                if "trades" in tables:
                    cur.execute("SELECT * FROM trades ORDER BY id DESC LIMIT ?", (limit,))
                    rows = [dict(r) for r in cur.fetchall()]
                    conn.close()
                    return rows
                conn.close()
            except Exception as e:
                logger.debug("Query trades error: %s", e)

        # Check orchestrator DB
        conn = self._get_read_only_conn(self.orchestrator_db)
        if conn:
            try:
                cur = conn.cursor()
                cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
                tables = {r[0] for r in cur.fetchall()}
                if "audit_log" in tables:
                    cur.execute("SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,))
                    rows = [dict(r) for r in cur.fetchall()]
                    conn.close()
                    return rows
                conn.close()
            except Exception as e:
                logger.debug("Query orchestrator audit error: %s", e)

        return []

    def get_active_positions(self) -> List[Dict[str, Any]]:
        """Reads active open positions from persistent JSON file."""
        if not self.active_pos_file.exists():
            return []
        try:
            with open(self.active_pos_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                if not data or not data.get("is_active"):
                    return []
                return data.get("active_positions", [data])
        except Exception as e:
            logger.debug("Error reading active positions: %s", e)
            return []

    def get_risk_status(self) -> Dict[str, Any]:
        """Combines kill switch and circuit breaker live states."""
        ks_state = {"is_tripped": False}
        if self.kill_switch_file.exists():
            try:
                with open(self.kill_switch_file, "r", encoding="utf-8") as f:
                    ks_state = json.load(f)
            except Exception:
                pass

        cb_state = {"is_halted": False, "remaining_budget_usd": 10.00}
        if self.circuit_breaker_file.exists():
            try:
                with open(self.circuit_breaker_file, "r", encoding="utf-8") as f:
                    cb_state = json.load(f)
            except Exception:
                pass

        return {
            "kill_switch": ks_state,
            "circuit_breaker": cb_state,
        }

    def get_database_metrics(self) -> Dict[str, Any]:
        """Returns metadata about database sizes and WAL journal presence."""
        def file_size(p: Path) -> str:
            if not p.exists():
                return "0 KB"
            size_kb = p.stat().st_size / 1024.0
            return f"{size_kb:.1f} KB"

        telemetry_wal = self.telemetry_db.with_suffix(".db-wal")
        return {
            "telemetry_db_size": file_size(self.telemetry_db),
            "telemetry_wal_active": telemetry_wal.exists(),
            "telemetry_wal_size": file_size(telemetry_wal),
            "orchestrator_db_size": file_size(self.orchestrator_db),
            "last_synced_utc": datetime.now(timezone.utc).strftime("%H:%M:%S UTC"),
        }
