import sqlite3
import json

conn = sqlite3.connect("data/telemetry/telemetry.db")
c = conn.cursor()
c.execute("SELECT * FROM paper_trades WHERE position_id = 'live_paper_81905'")
cols = [d[0] for d in c.description]
row = c.fetchone()
print("Position 81905:", dict(zip(cols, row)))

# Filter trades strictly between 16:04 and 18:05 on 2026-10-16 (the 2-hour session requested by user)
c.execute("""
    SELECT * FROM paper_trades 
    WHERE entry_ts >= '2026-10-16T16:04:00' AND entry_ts <= '2026-10-16T18:05:00'
    ORDER BY entry_ts ASC
""")
session_trades = [dict(zip(cols, r)) for r in c.fetchall()]
print(f"\nStrict 2-hour window trades count: {len(session_trades)}")
for t in session_trades:
    print(f"{t['position_id']} | {t['token_mint'][:10]} | {t['entry_ts']} -> {t['exit_ts']} | In: {t['entry_price_sol']:.2e} | Out: {t['exit_price_sol']:.2e} | PnL: ${t['net_pnl_usd']:.2f} ({t['net_return_pct']:.1f}%) | {t['exit_reason']}")

# All live_paper trades (since we restarted live mode with 'live_paper_')
c.execute("SELECT * FROM paper_trades WHERE position_id LIKE 'live_paper_%' ORDER BY entry_ts ASC")
all_live = [dict(zip(cols, r)) for r in c.fetchall()]
print(f"\nAll live_paper_* trades count: {len(all_live)}")
