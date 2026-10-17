import sqlite3
from datetime import datetime, timezone

conn = sqlite3.connect("data/telemetry/telemetry.db")
c = conn.cursor()
c.execute("SELECT pool_address, token_mint, state, graduation_ts, last_updated_ts FROM pools WHERE state = 'ACTIVE_WINDOW'")
now = datetime.now(timezone.utc)
rows = c.fetchall()
print(f"Total pools marked 'ACTIVE_WINDOW' in DB: {len(rows)}")
for r in rows[:15]:
    try:
        grad_dt = datetime.fromisoformat(r[3].replace("Z", "+00:00"))
        age_hours = (now - grad_dt).total_seconds() / 3600.0
    except Exception:
        age_hours = -1
    print(f"Mint: {r[1][:12]} | Grad: {r[3]} | Age: {age_hours:.1f} hours | Updated: {r[4]}")
