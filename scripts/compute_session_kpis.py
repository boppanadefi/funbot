import sqlite3
import json
from pathlib import Path
from datetime import datetime, timezone

conn = sqlite3.connect("data/telemetry/telemetry.db")
c = conn.cursor()

# Query all trades from today's real session
c.execute("""
    SELECT position_id, token_mint, regime, entry_ts, exit_ts, 
           entry_price_sol, exit_price_sol, net_pnl_usd, net_return_pct, 
           exit_reason, ratchet_activated, duration_minutes
    FROM paper_trades 
    WHERE entry_ts >= '2026-10-16'
    ORDER BY entry_ts ASC
""")
cols = [d[0] for d in c.description]
rows = [dict(zip(cols, r)) for r in c.fetchall()]

# Check active paper position
active_pos_file = Path("data/live/active_paper_position.json")
active_pos = None
if active_pos_file.exists():
    try:
        with open(active_pos_file, "r") as f:
            data = json.load(f)
            if data.get("is_active"):
                active_pos = data
    except Exception as e:
        print("Error loading active pos:", e)

# Circuit breaker state
cb_file = Path("data/live/circuit_breaker_state.json")
cb_state = {}
if cb_file.exists():
    try:
        with open(cb_file, "r") as f:
            cb_state = json.load(f)
    except Exception as e:
        print("Error loading cb state:", e)

print("=" * 60)
print(f"CLOSED TRADES TODAY ({len(rows)} trades):")
print("=" * 60)
total_pnl = 0.0
wins = 0
losses = 0
returns = []
peak_pnl = 0.0
cum_pnl = 0.0
max_dd = 0.0

for r in rows:
    pnl = r["net_pnl_usd"]
    ret = r["net_return_pct"]
    total_pnl += pnl
    cum_pnl += pnl
    if cum_pnl > peak_pnl:
        peak_pnl = cum_pnl
    dd = peak_pnl - cum_pnl
    if dd > max_dd:
        max_dd = dd

    if pnl > 0:
        wins += 1
    elif pnl < 0:
        losses += 1
    returns.append(ret)
    print(f"ID: {r['position_id']} | Mint: {r['token_mint'][:12]}... | Regime: {r['regime']} | Reason: {r['exit_reason']} | PnL: ${pnl:.2f} | Ret: {ret:.1f}% | Dur: {r['duration_minutes']:.1f}m")

win_rate = (wins / len(rows) * 100.0) if rows else 0.0
avg_ret = (sum(returns) / len(returns)) if returns else 0.0
avg_win = (sum(r["net_pnl_usd"] for r in rows if r["net_pnl_usd"] > 0) / wins) if wins > 0 else 0.0
avg_loss = (sum(r["net_pnl_usd"] for r in rows if r["net_pnl_usd"] < 0) / losses) if losses > 0 else 0.0
profit_factor = (sum(r["net_pnl_usd"] for r in rows if r["net_pnl_usd"] > 0) / abs(sum(r["net_pnl_usd"] for r in rows if r["net_pnl_usd"] < 0))) if losses > 0 and sum(r["net_pnl_usd"] for r in rows if r["net_pnl_usd"] < 0) != 0 else float("inf")
expectancy = (total_pnl / len(rows)) if rows else 0.0

print("\n" + "=" * 60)
print("PERFORMANCE KPIS:")
print("=" * 60)
print(f"Total Closed Trades: {len(rows)}")
print(f"Win / Loss: {wins} Wins / {losses} Losses")
print(f"Win Rate: {win_rate:.1f}%")
print(f"Realized Net P&L: ${total_pnl:.2f}")
print(f"Average Win: ${avg_win:.2f}")
print(f"Average Loss: ${avg_loss:.2f}")
print(f"Profit Factor: {profit_factor:.2f}")
print(f"Expectancy: ${expectancy:.2f} per trade")
print(f"Maximum Drawdown: ${max_dd:.2f}")

print("\n" + "=" * 60)
print("ACTIVE POSITION:")
print("=" * 60)
if active_pos:
    print(f"Position ID: {active_pos.get('position_id')}")
    print(f"Token Mint: {active_pos.get('token_mint')}")
    print(f"State: {active_pos.get('state')}")
    print(f"Regime: {active_pos.get('regime')}")
    print(f"Entry Price (SOL): {active_pos.get('entry_price_sol')}")
    print(f"Current Price (SOL): {active_pos.get('current_price_sol')}")
    print(f"Unrealized P&L: ${active_pos.get('unrealized_pnl_usd', 0.0):.2f} ({active_pos.get('unrealized_return_pct', 0.0):.2f}%)")
    print(f"Elapsed: {active_pos.get('elapsed_minutes', 0.0):.1f}m")
    print(f"Ratchet Activated: {active_pos.get('ratchet_activated')}")
else:
    print("No open active position currently.")

print("\n" + "=" * 60)
print("CIRCUIT BREAKER STATE:")
print("=" * 60)
print(json.dumps(cb_state, indent=2))
