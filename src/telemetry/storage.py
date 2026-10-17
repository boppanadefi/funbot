"""
Telemetry SQLite Persistence Layer.
Manages atomic storage of observed pools, swaps, cliff metrics, and feed health audits.
"""

from datetime import datetime, timezone, timedelta
import json
from pathlib import Path
import sqlite3
from typing import Dict, List, Optional
from src.utils.config import ConfigLoader


class TelemetryStorage:
    def __init__(self, db_path: Optional[str] = None):
        if db_path is None:
            loader = ConfigLoader()
            config = loader.load_telemetry_config()
            db_path = config.sqlite_db_path

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

            # Pools Table
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS pools (
                pool_address TEXT PRIMARY KEY,
                token_mint TEXT NOT NULL,
                quote_mint TEXT NOT NULL,
                is_canonical INTEGER NOT NULL,
                graduation_slot INTEGER NOT NULL,
                graduation_ts TEXT NOT NULL,
                initial_sol_reserve REAL NOT NULL,
                initial_token_reserve REAL NOT NULL,
                current_sol_reserve REAL NOT NULL,
                market_cap_sol REAL NOT NULL,
                state TEXT NOT NULL,
                price_impact_20usd_pct REAL DEFAULT 0.0,
                modeled_friction_pct REAL DEFAULT 0.0,
                last_updated_ts TEXT NOT NULL
            );
            """)

            # Swaps Table
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS swaps (
                tx_signature TEXT PRIMARY KEY,
                pool_address TEXT NOT NULL,
                is_buy INTEGER NOT NULL,
                sol_amount REAL NOT NULL,
                token_amount REAL NOT NULL,
                trader_pubkey TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                slot INTEGER NOT NULL,
                FOREIGN KEY(pool_address) REFERENCES pools(pool_address)
            );
            """)

            # Cliff Metrics Table
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS cliff_metrics (
                pool_address TEXT PRIMARY KEY,
                vol_0_to_5m REAL DEFAULT 0.0,
                vol_5_to_8m REAL DEFAULT 0.0,
                vol_8_to_25m REAL DEFAULT 0.0,
                cliff_decay_pct REAL DEFAULT 0.0,
                recorded_ts TEXT NOT NULL,
                FOREIGN KEY(pool_address) REFERENCES pools(pool_address)
            );
            """)

            # Feed Health Audit Table
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS feed_audit (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                event_type TEXT NOT NULL,
                latency_ms REAL,
                details TEXT
            );
            """)

            # Cluster Risk Evaluations Table (Phase 2)
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS cluster_evaluations (
                pool_address TEXT PRIMARY KEY,
                token_mint TEXT NOT NULL,
                total_buyers INTEGER NOT NULL,
                burner_count INTEGER NOT NULL,
                burner_volume_ratio REAL NOT NULL,
                top_parent_pubkey TEXT,
                top_parent_concentration REAL NOT NULL,
                sybil_detected INTEGER NOT NULL,
                mint_revoked INTEGER NOT NULL,
                freeze_revoked INTEGER NOT NULL,
                composite_risk_score REAL NOT NULL,
                is_approved INTEGER NOT NULL,
                rejection_reasons TEXT,
                evaluated_ts TEXT NOT NULL,
                FOREIGN KEY(pool_address) REFERENCES pools(pool_address)
            );
            """)

            conn.commit()

    def upsert_pool(self, pool_data: Dict) -> None:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
            INSERT INTO pools (
                pool_address, token_mint, quote_mint, is_canonical,
                graduation_slot, graduation_ts, initial_sol_reserve,
                initial_token_reserve, current_sol_reserve, market_cap_sol,
                state, price_impact_20usd_pct, modeled_friction_pct, last_updated_ts
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(pool_address) DO UPDATE SET
                current_sol_reserve = excluded.current_sol_reserve,
                market_cap_sol = excluded.market_cap_sol,
                state = excluded.state,
                price_impact_20usd_pct = excluded.price_impact_20usd_pct,
                modeled_friction_pct = excluded.modeled_friction_pct,
                last_updated_ts = excluded.last_updated_ts;
            """, (
                pool_data["pool_address"],
                pool_data["token_mint"],
                pool_data.get("quote_mint", "So11111111111111111111111111111111111111112"),
                1 if pool_data.get("is_canonical", True) else 0,
                pool_data.get("graduation_slot", 0),
                pool_data["graduation_ts"],
                pool_data.get("initial_sol_reserve", pool_data.get("current_sol_reserve", 85.0)),
                pool_data.get("initial_token_reserve", 206_900_000.0),
                pool_data["current_sol_reserve"],
                pool_data["market_cap_sol"],
                pool_data["state"],
                pool_data.get("price_impact_20usd_pct", 0.0),
                pool_data.get("modeled_friction_pct", 0.0),
                pool_data.get("last_updated_ts", datetime.now(timezone.utc).isoformat()),
            ))
            conn.commit()

    def record_swap(self, swap_data: Dict) -> None:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
            INSERT OR IGNORE INTO swaps (
                tx_signature, pool_address, is_buy, sol_amount,
                token_amount, trader_pubkey, timestamp, slot
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                swap_data["tx_signature"],
                swap_data["pool_address"],
                1 if swap_data["is_buy"] else 0,
                swap_data["sol_amount"],
                swap_data["token_amount"],
                swap_data["trader_pubkey"],
                swap_data["timestamp"],
                swap_data["slot"],
            ))
            conn.commit()

    def upsert_cliff_metrics(self, cliff_data: Dict) -> None:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
            INSERT INTO cliff_metrics (
                pool_address, vol_0_to_5m, vol_5_to_8m, vol_8_to_25m,
                cliff_decay_pct, recorded_ts
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(pool_address) DO UPDATE SET
                vol_0_to_5m = excluded.vol_0_to_5m,
                vol_5_to_8m = excluded.vol_5_to_8m,
                vol_8_to_25m = excluded.vol_8_to_25m,
                cliff_decay_pct = excluded.cliff_decay_pct,
                recorded_ts = excluded.recorded_ts;
            """, (
                cliff_data["pool_address"],
                cliff_data["vol_0_to_5m"],
                cliff_data["vol_5_to_8m"],
                cliff_data["vol_8_to_25m"],
                cliff_data["cliff_decay_pct"],
                cliff_data.get("recorded_ts", datetime.now(timezone.utc).isoformat()),
            ))
            conn.commit()

    def log_feed_audit(self, event_type: str, latency_ms: Optional[float] = None, details: str = "") -> None:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
            INSERT INTO feed_audit (timestamp, event_type, latency_ms, details)
            VALUES (?, ?, ?, ?)
            """, (
                datetime.now(timezone.utc).isoformat(),
                event_type,
                latency_ms,
                details,
            ))
            conn.commit()

    def upsert_cluster_evaluation(self, eval_data: Dict) -> None:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            reasons_str = json.dumps(eval_data.get("rejection_reasons", []))
            cursor.execute("""
            INSERT INTO cluster_evaluations (
                pool_address, token_mint, total_buyers, burner_count,
                burner_volume_ratio, top_parent_pubkey, top_parent_concentration,
                sybil_detected, mint_revoked, freeze_revoked, composite_risk_score,
                is_approved, rejection_reasons, evaluated_ts
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(pool_address) DO UPDATE SET
                total_buyers = excluded.total_buyers,
                burner_count = excluded.burner_count,
                burner_volume_ratio = excluded.burner_volume_ratio,
                top_parent_pubkey = excluded.top_parent_pubkey,
                top_parent_concentration = excluded.top_parent_concentration,
                sybil_detected = excluded.sybil_detected,
                mint_revoked = excluded.mint_revoked,
                freeze_revoked = excluded.freeze_revoked,
                composite_risk_score = excluded.composite_risk_score,
                is_approved = excluded.is_approved,
                rejection_reasons = excluded.rejection_reasons,
                evaluated_ts = excluded.evaluated_ts;
            """, (
                eval_data["pool_address"],
                eval_data["token_mint"],
                eval_data["total_buyers"],
                eval_data["burner_count"],
                eval_data["burner_volume_ratio"],
                eval_data.get("top_parent_pubkey"),
                eval_data["top_parent_concentration"],
                1 if eval_data.get("sybil_detected", False) else 0,
                1 if eval_data.get("mint_revoked", True) else 0,
                1 if eval_data.get("freeze_revoked", True) else 0,
                eval_data["composite_risk_score"],
                1 if eval_data.get("is_approved", True) else 0,
                reasons_str,
                eval_data.get("evaluated_ts", datetime.now(timezone.utc).isoformat()),
            ))
            conn.commit()

    def get_cluster_evaluation(self, pool_address: str) -> Optional[Dict]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
            SELECT * FROM cluster_evaluations WHERE pool_address = ?;
            """, (pool_address,))
            row = cursor.fetchone()
            if not row:
                return None
            res = dict(row)
            try:
                res["rejection_reasons"] = json.loads(res.get("rejection_reasons", "[]"))
            except Exception:
                res["rejection_reasons"] = []
            return res

    def get_all_pools(self) -> List[Dict]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
            SELECT p.*, c.vol_0_to_5m, c.vol_5_to_8m, c.vol_8_to_25m, c.cliff_decay_pct
            FROM pools p
            LEFT JOIN cliff_metrics c ON p.pool_address = c.pool_address
            ORDER BY p.graduation_ts DESC;
            """)
            rows = cursor.fetchall()
            return [dict(r) for r in rows]

    def get_active_candidates(self, min_age_minutes: float = 8.0, max_age_minutes: float = 25.0) -> List[Dict]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
            SELECT p.*, c.vol_0_to_5m, c.vol_5_to_8m, c.vol_8_to_25m, c.cliff_decay_pct
            FROM pools p
            LEFT JOIN cliff_metrics c ON p.pool_address = c.pool_address
            ORDER BY p.last_updated_ts DESC;
            """)
            rows = cursor.fetchall()
            now = datetime.now(timezone.utc)
            candidates = []
            for r in rows:
                p = dict(r)
                grad_ts_str = p.get("graduation_ts")
                if grad_ts_str:
                    try:
                        grad_dt = datetime.fromisoformat(grad_ts_str.replace("Z", "+00:00"))
                        age_min = max(0.0, (now - grad_dt).total_seconds() / 60.0)
                    except Exception:
                        continue
                else:
                    continue

                p["minutes_post_graduation"] = round(age_min, 1)
                p["age_minutes"] = round(age_min, 1)

                if (min_age_minutes <= age_min <= max_age_minutes) or (p.get("state") == "ACTIVE_WINDOW" and age_min <= max_age_minutes):
                    p["state"] = "ACTIVE_WINDOW"
                    candidates.append(p)

            return candidates

    def expire_stale_pools(self, max_age_minutes: float = 25.0) -> int:
        """Marks pools older than max_age_minutes as EXPIRED in the database."""
        now = datetime.now(timezone.utc)
        cutoff = (now - timedelta(minutes=max_age_minutes)).isoformat()
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
            UPDATE pools 
            SET state = 'EXPIRED' 
            WHERE state = 'ACTIVE_WINDOW' AND graduation_ts < ?;
            """, (cutoff,))
            conn.commit()
            return cursor.rowcount

    def export_gate_dataset(self, observation_days: int = 14) -> Dict:
        """Exports full telemetry snapshot compatible with Phase1FalsificationGate."""
        pools = self.get_all_pools()
        formatted_pools = []
        now = datetime.now(timezone.utc)

        for p in pools:
            try:
                grad_ts = datetime.fromisoformat(p["graduation_ts"].replace("Z", "+00:00"))
                age_min = (now - grad_ts).total_seconds() / 60.0
            except Exception:
                age_min = 15.0

            formatted_pools.append({
                "pool_address": p["pool_address"],
                "token_mint": p["token_mint"],
                "quote_asset": "SOL" if p["is_canonical"] == 1 else "OTHER",
                "is_canonical": bool(p["is_canonical"]),
                "minutes_post_graduation": round(age_min, 1),
                "sol_reserve": p["current_sol_reserve"],
                "market_cap_sol": p["market_cap_sol"],
                "vol_0_to_5m": p.get("vol_0_to_5m", 0.0) or 0.0,
                "vol_5_to_8m": p.get("vol_5_to_8m", 0.0) or 0.0,
                "vol_8_to_25m": p.get("vol_8_to_25m", 0.0) or 0.0,
                "buyback_cliff_decay_pct": p.get("cliff_decay_pct", 0.0) or 0.0,
                "price_impact_20usd_pct": p.get("price_impact_20usd_pct", 1.5),
                "modeled_friction_pct": p.get("modeled_friction_pct", 5.5),
            })

        return {
            "observation_days": observation_days,
            "export_ts": now.isoformat(),
            "total_pools": len(formatted_pools),
            "pools": formatted_pools,
        }
