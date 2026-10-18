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
                signal_ts TEXT,
                entry_sim_ts TEXT,
                entry_ts TEXT NOT NULL,
                exit_ts TEXT NOT NULL,
                entry_price REAL,
                entry_price_sol REAL NOT NULL,
                position_size_sol REAL,
                tp1_fill_prices TEXT,
                runner_exit_price REAL,
                exit_price_sol REAL NOT NULL,
                sol_spent REAL NOT NULL,
                sol_received REAL NOT NULL,
                all_tx_fees_sol REAL,
                all_jito_tips_sol REAL,
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

            # Auto-migrate columns if table already existed without them
            new_cols = [
                ("signal_ts", "TEXT"),
                ("entry_sim_ts", "TEXT"),
                ("entry_price", "REAL"),
                ("position_size_sol", "REAL"),
                ("tp1_fill_prices", "TEXT"),
                ("runner_exit_price", "REAL"),
                ("all_tx_fees_sol", "REAL"),
                ("all_jito_tips_sol", "REAL"),
            ]
            for col_name, col_type in new_cols:
                try:
                    cursor.execute(f"ALTER TABLE paper_trades ADD COLUMN {col_name} {col_type};")
                except sqlite3.OperationalError:
                    pass  # Column already exists
            conn.commit()

    def record_trade(self, pos: PaperPosition, sol_price_usd: float = 155.0) -> None:
        if not pos.exit_leg or not pos.entry_leg:
            return

        import json

        sol_spent = pos.sol_spent
        position_size_sol = pos.position_size_sol
        entry_price = pos.entry_price_sol
        signal_ts = pos.signal_ts.isoformat() if pos.signal_ts else pos.entry_time.isoformat()
        entry_sim_ts = pos.entry_sim_ts.isoformat() if pos.entry_sim_ts else pos.entry_time.isoformat()

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

        # Strict Accounting Formulas:
        # All TX fees = venue fees + priority fees + base fees
        all_tx_fees_sol = venue_fees + priority
        # All Jito tips across entry and exits
        all_jito_tips_sol = tips
        # Net PnL = (SOL received across all exits) - (SOL spent on entry) - (All TX fees) - (All Jito tips)
        net_pnl_sol = sol_received - sol_spent - all_tx_fees_sol - all_jito_tips_sol
        net_pnl_usd = net_pnl_sol * sol_price_usd
        net_return_pct = (net_pnl_sol / sol_spent) * 100.0 if sol_spent > 0 else 0.0

        pos.realized_net_pnl_usd = round(net_pnl_usd, 4)
        pos.realized_net_return_pct = round(net_return_pct, 2)

        tp1_fill_prices_json = json.dumps(pos.tp1_fill_prices)
        runner_exit_price = pos.runner_exit_price

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
            INSERT INTO paper_trades (
                position_id, pool_address, token_mint, regime,
                signal_ts, entry_sim_ts, entry_ts, exit_ts,
                entry_price, entry_price_sol, position_size_sol,
                tp1_fill_prices, runner_exit_price, exit_price_sol,
                sol_spent, sol_received, all_tx_fees_sol, all_jito_tips_sol,
                venue_fees_sol, tips_sol, priority_fees_sol,
                net_pnl_sol, net_pnl_usd, net_return_pct,
                exit_reason, ratchet_activated, duration_minutes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(position_id) DO UPDATE SET
                signal_ts = excluded.signal_ts,
                entry_sim_ts = excluded.entry_sim_ts,
                entry_price = excluded.entry_price,
                position_size_sol = excluded.position_size_sol,
                tp1_fill_prices = excluded.tp1_fill_prices,
                runner_exit_price = excluded.runner_exit_price,
                all_tx_fees_sol = excluded.all_tx_fees_sol,
                all_jito_tips_sol = excluded.all_jito_tips_sol,
                exit_price_sol = excluded.exit_price_sol,
                sol_received = excluded.sol_received,
                venue_fees_sol = excluded.venue_fees_sol,
                tips_sol = excluded.tips_sol,
                priority_fees_sol = excluded.priority_fees_sol,
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
                signal_ts,
                entry_sim_ts,
                pos.entry_time.isoformat(),
                (pos.exit_time or datetime.now(timezone.utc)).isoformat(),
                round(entry_price, 8),
                round(pos.entry_price_sol, 8),
                round(position_size_sol, 6),
                tp1_fill_prices_json,
                round(runner_exit_price, 8) if runner_exit_price else None,
                round(exit_price_sol, 8),
                round(sol_spent, 6),
                round(sol_received, 6),
                round(all_tx_fees_sol, 6),
                round(all_jito_tips_sol, 6),
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

    def get_trade(self, position_id: str) -> Optional[Dict]:
        """Retrieves a single trade by position_id."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM paper_trades WHERE position_id = ?;", (position_id,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def get_all_trades(self) -> List[Dict]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM paper_trades ORDER BY entry_ts DESC;")
            return [dict(r) for r in cursor.fetchall()]

    def get_strict_accounting_records(self) -> List[Dict]:
        """
        Returns Phase 3 strict accounting trade records:
        signal_ts, entry_sim_ts, entry_price, position_size_sol,
        tp1_fill_prices (parsed list), runner_exit_price, net_pnl_sol.
        """
        import json
        trades = self.get_all_trades()
        records = []
        for t in trades:
            tp1_prices = json.loads(t["tp1_fill_prices"]) if t.get("tp1_fill_prices") else []
            records.append({
                "position_id": t["position_id"],
                "pool_address": t["pool_address"],
                "token_mint": t["token_mint"],
                "signal_ts": t.get("signal_ts") or t["entry_ts"],
                "entry_sim_ts": t.get("entry_sim_ts") or t["entry_ts"],
                "entry_price": t.get("entry_price") or t["entry_price_sol"],
                "position_size_sol": t.get("position_size_sol") or t["sol_spent"],
                "tp1_fill_prices": tp1_prices,
                "runner_exit_price": t.get("runner_exit_price"),
                "net_pnl_sol": t["net_pnl_sol"],
                "net_pnl_usd": t["net_pnl_usd"],
                "all_tx_fees_sol": t.get("all_tx_fees_sol") or (t["venue_fees_sol"] + t["priority_fees_sol"]),
                "all_jito_tips_sol": t.get("all_jito_tips_sol") or t["tips_sol"],
                "exit_reason": t["exit_reason"],
            })
        return records

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
