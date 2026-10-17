"""
Persistent Paper Trading Ledger.
Records full accounting of all simulated trades (gross proceeds, venue fees, tips, net cash flow).
"""

from datetime import datetime, timezone
from pathlib import Path
import sqlite3
from typing import Dict, List, Optional
from src.engine.models import PaperPosition


class PaperLedger:
    """
    Manages persistent SQLite storage of paper trading executions.
    """

    def __init__(self, db_path: str = "data/telemetry/telemetry.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_tables()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=15.0)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_tables(self) -> None:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS paper_trades (
                position_id TEXT PRIMARY KEY,
                pool_address TEXT NOT NULL,
                token_mint TEXT NOT NULL,
                regime TEXT NOT NULL,
                entry_ts TEXT NOT NULL,
                exit_ts TEXT NOT NULL,
                entry_price_sol REAL NOT NULL,
                exit_price_sol REAL NOT NULL,
                sol_spent REAL NOT NULL,
                sol_received REAL NOT NULL,
                venue_fees_sol REAL NOT NULL,
                tips_sol REAL NOT NULL,
                priority_fees_sol REAL NOT NULL,
                net_pnl_sol REAL NOT NULL,
                net_pnl_usd REAL NOT NULL,
                net_return_pct REAL NOT NULL,
                exit_reason TEXT NOT NULL,
                ratchet_activated INTEGER NOT NULL,
                duration_minutes REAL NOT NULL
            );
            """)
            conn.commit()

    def record_trade(self, pos: PaperPosition, sol_price_usd: float = 155.0) -> None:
        if not pos.exit_leg or not pos.entry_leg:
            return

        sol_spent = pos.sol_spent

        if pos.tp1_leg and pos.runner_leg:
            # Friction-Busting Scale-Out (Two-Stage Exit)
            sol_received = pos.tp1_leg.sol_amount + pos.runner_leg.sol_amount
            venue_fees = (
                pos.entry_leg.venue_fee_sol
                + pos.tp1_leg.venue_fee_sol
                + pos.runner_leg.venue_fee_sol
            )
            tips = (
                pos.entry_leg.jito_tip_sol
                + pos.tp1_leg.jito_tip_sol
                + pos.runner_leg.jito_tip_sol
            )
            priority = (
                pos.entry_leg.priority_fee_sol
                + pos.entry_leg.base_fee_sol
                + pos.tp1_leg.priority_fee_sol
                + pos.tp1_leg.base_fee_sol
                + pos.runner_leg.priority_fee_sol
                + pos.runner_leg.base_fee_sol
            )
            exit_price_sol = sol_received / pos.tokens_held if pos.tokens_held > 0 else pos.exit_leg.price_sol
        else:
            # Single Full Exit (Stop or Timeout before TP1)
            sol_received = pos.exit_leg.sol_amount
            venue_fees = pos.entry_leg.venue_fee_sol + pos.exit_leg.venue_fee_sol
            tips = pos.entry_leg.jito_tip_sol + pos.exit_leg.jito_tip_sol
            priority = (
                pos.entry_leg.priority_fee_sol
                + pos.entry_leg.base_fee_sol
                + pos.exit_leg.priority_fee_sol
                + pos.exit_leg.base_fee_sol
            )
            exit_price_sol = pos.exit_leg.price_sol

        net_pnl_sol = sol_received - sol_spent - venue_fees - tips - priority
        net_pnl_usd = net_pnl_sol * sol_price_usd
        net_return_pct = (net_pnl_sol / sol_spent) * 100.0 if sol_spent > 0 else 0.0

        pos.realized_net_pnl_usd = round(net_pnl_usd, 4)
        pos.realized_net_return_pct = round(net_return_pct, 2)

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
            INSERT INTO paper_trades (
                position_id, pool_address, token_mint, regime,
                entry_ts, exit_ts, entry_price_sol, exit_price_sol,
                sol_spent, sol_received, venue_fees_sol, tips_sol,
                priority_fees_sol, net_pnl_sol, net_pnl_usd, net_return_pct,
                exit_reason, ratchet_activated, duration_minutes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(position_id) DO UPDATE SET
                exit_price_sol = excluded.exit_price_sol,
                sol_received = excluded.sol_received,
                net_pnl_sol = excluded.net_pnl_sol,
                net_pnl_usd = excluded.net_pnl_usd,
                net_return_pct = excluded.net_return_pct,
                exit_reason = excluded.exit_reason,
                ratchet_activated = excluded.ratchet_activated,
                duration_minutes = excluded.duration_minutes;
            """, (
                pos.position_id,
                pos.pool_address,
                pos.token_mint,
                pos.regime,
                pos.entry_time.isoformat(),
                (pos.exit_time or datetime.now(timezone.utc)).isoformat(),
                pos.entry_price_sol,
                pos.exit_leg.price_sol,
                round(sol_spent, 6),
                round(sol_received, 6),
                round(venue_fees, 6),
                round(tips, 6),
                round(priority, 6),
                round(net_pnl_sol, 6),
                round(net_pnl_usd, 4),
                round(net_return_pct, 2),
                pos.exit_reason or "MANUAL",
                1 if pos.ratchet_activated else 0,
                round(pos.duration_minutes, 2),
            ))
            conn.commit()

    def get_all_trades(self) -> List[Dict]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM paper_trades ORDER BY entry_ts DESC;")
            return [dict(r) for r in cursor.fetchall()]

    def get_summary_metrics(self) -> Dict:
        trades = self.get_all_trades()
        if not trades:
            return {
                "total_trades": 0,
                "win_rate_pct": 0.0,
                "net_pnl_usd": 0.0,
                "profit_factor": 0.0,
                "max_drawdown_usd": 0.0,
            }

        wins = [t for t in trades if t["net_pnl_usd"] > 0]
        losses = [t for t in trades if t["net_pnl_usd"] <= 0]
        total = len(trades)
        win_rate = (len(wins) / total) * 100.0

        net_pnl = sum(t["net_pnl_usd"] for t in trades)
        gross_win = sum(t["net_pnl_usd"] for t in wins)
        gross_loss = abs(sum(t["net_pnl_usd"] for t in losses))
        profit_factor = (gross_win / gross_loss) if gross_loss > 0 else 99.0

        # Calculate Max Drawdown
        peak = 0.0
        running_cum = 0.0
        max_dd = 0.0
        for t in reversed(trades):  # Chronological order
            running_cum += t["net_pnl_usd"]
            if running_cum > peak:
                peak = running_cum
            dd = peak - running_cum
            if dd > max_dd:
                max_dd = dd

        return {
            "total_trades": total,
            "win_rate_pct": round(win_rate, 1),
            "net_pnl_usd": round(net_pnl, 2),
            "profit_factor": round(profit_factor, 2),
            "max_drawdown_usd": round(max_dd, 2),
        }
