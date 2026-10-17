import sqlite3
import json

conn = sqlite3.connect("data/telemetry/telemetry.db")
c = conn.cursor()

c.execute("""
    SELECT position_id, token_mint, regime, entry_ts, exit_ts, 
           entry_price_sol, exit_price_sol, net_pnl_usd, net_return_pct, 
           exit_reason, ratchet_activated, duration_minutes
    FROM paper_trades 
    WHERE entry_ts >= '2026-10-16T16:04:00' AND entry_ts <= '2026-10-16T18:05:00'
    ORDER BY entry_ts ASC
""")
cols = [d[0] for d in c.description]
session_trades = [dict(zip(cols, r)) for r in c.fetchall()]

# Metrics
wins = [t for t in session_trades if t["net_pnl_usd"] > 0]
losses = [t for t in session_trades if t["net_pnl_usd"] <= 0]
total_pnl = sum(t["net_pnl_usd"] for t in session_trades)
win_rate = (len(wins) / len(session_trades)) * 100
avg_win = sum(t["net_pnl_usd"] for t in wins) / len(wins)
avg_loss = sum(t["net_pnl_usd"] for t in losses) / len(losses)
gross_win = sum(t["net_pnl_usd"] for t in wins)
gross_loss = abs(sum(t["net_pnl_usd"] for t in losses))
profit_factor = gross_win / gross_loss if gross_loss > 0 else 999.0
expectancy = total_pnl / len(session_trades)

peak = 0.0
cum = 0.0
max_dd = 0.0
for t in session_trades:
    cum += t["net_pnl_usd"]
    if cum > peak:
        peak = cum
    dd = peak - cum
    if dd > max_dd:
        max_dd = dd

print("--- 2-HOUR SESSION METRICS ---")
print(f"Trades: {len(session_trades)}")
print(f"Wins: {len(wins)} | Losses: {len(losses)}")
print(f"Win Rate: {win_rate:.1f}%")
print(f"Net P&L: ${total_pnl:.2f}")
print(f"Gross Wins: ${gross_win:.2f} | Gross Losses: ${gross_loss:.2f}")
print(f"Profit Factor: {profit_factor:.2f}")
print(f"Expectancy: ${expectancy:.2f} / trade")
print(f"Avg Win: ${avg_win:.2f} | Avg Loss: ${avg_loss:.2f}")
print(f"Max Drawdown: ${max_dd:.2f}")
