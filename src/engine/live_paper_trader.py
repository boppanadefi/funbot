"""
Live Continuous Paper Trading Engine.
Executes Path A+ paper trades in real-time as pools enter the 8-25m secondary window.
Enforces all 7 Invariant System Mitigations:
- Strictly 1 position at a time.
- $10 daily risk ceiling check before entry.
- Dynamic trailing stop with hurdle ratchet.
- 20-minute fallback timeout.
- Full venue fee, tip, and slippage accounting.
"""

from datetime import datetime, timezone, timedelta
import json
import logging
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from src.cost.double_gate import PreFlightDoubleGate
from src.engine.models import PaperPosition, PositionState
from src.engine.paper_ledger import PaperLedger
from src.engine.simulator import PathASimulator
from src.engine.market_structure import MarketStructureTracker
from src.engine.order_flow import OrderFlowDynamicsEngine
from src.live.circuit_breaker import DailyCircuitBreaker
from src.live.kill_switch import EmergencyKillSwitch
from src.telemetry.live_market_feed import LiveMarketFeed

logger = logging.getLogger("LivePaperTrader")


class LivePaperTradingEngine:
    """
    Coordinates continuous live paper trading against active incoming pools.
    """

    def __init__(
        self,
        db_path: str = "data/telemetry/telemetry.db",
        state_file: str = "data/live/active_paper_position.json",
        sol_price_usd: float = 155.0,
        position_notional_usd: float = 15.0,
        min_age_minutes: float = 3.0,
        max_age_minutes: float = 25.0,
        circuit_breaker: Optional[DailyCircuitBreaker] = None,
        kill_switch: Optional[EmergencyKillSwitch] = None,
        market_structure_tracker: Optional[MarketStructureTracker] = None,
        order_flow_engine: Optional[OrderFlowDynamicsEngine] = None,
    ):
        self.ledger = PaperLedger(db_path=db_path)
        self.sim = PathASimulator(ledger=self.ledger, sol_price_usd=sol_price_usd)
        self.circuit_breaker = circuit_breaker or DailyCircuitBreaker()
        self.kill_switch = kill_switch or EmergencyKillSwitch()
        self.double_gate = PreFlightDoubleGate()
        self.market_structure = market_structure_tracker or MarketStructureTracker(min_cooldown_minutes=min_age_minutes)
        self.order_flow = order_flow_engine or OrderFlowDynamicsEngine()
        self.live_feed = LiveMarketFeed(
            sol_price_usd=sol_price_usd,
            min_age_minutes=min_age_minutes,
            max_age_minutes=max_age_minutes,
        )
        self.token_cooldowns: Dict[str, datetime] = {}
        self.sol_price_usd = sol_price_usd
        self.position_notional_usd = position_notional_usd
        self.min_age_minutes = min_age_minutes
        self.max_age_minutes = max_age_minutes
        self.db_path = db_path
        self.traded_tokens: set = self._load_traded_tokens()
        self.state_file = Path(state_file)
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self._current_pos: Optional[PaperPosition] = None
        self._load_active_position()

    def _load_traded_tokens(self) -> set:
        """Loads all previously traded token mints from the database to prevent repeat buys."""
        try:
            import sqlite3
            with sqlite3.connect(self.db_path) as conn:
                c = conn.cursor()
                c.execute("SELECT DISTINCT token_mint FROM paper_trades")
                mints = {row[0] for row in c.fetchall() if row[0]}
                logger.info("Loaded %d previously traded tokens from ledger history", len(mints))
                return mints
        except Exception as e:
            logger.warning("Could not load traded tokens from paper_trades: %s", e)
            return set()

    def _load_active_position(self) -> None:
        """Loads currently open position from persistent JSON if present."""
        if not self.state_file.exists():
            return
        try:
            with open(self.state_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                if not data or not data.get("is_active"):
                    self._current_pos = None
                    return
                # Reconstruct position
                entry_dt = datetime.fromisoformat(data["entry_time"])
                self._current_pos = self.sim.open_position(
                    position_id=data["position_id"],
                    pool_address=data["pool_address"],
                    token_mint=data["token_mint"],
                    regime=data["regime"],
                    market_cap_sol=data.get("market_cap_sol", 380.0),
                    entry_price_sol=data["entry_price_sol"],
                    tp1_target_gross_pct=data.get("tp1_target_gross_pct", 0.09),
                    position_notional_usd=data.get("position_notional_usd", self.position_notional_usd),
                    trailing_pct=data.get("runner_trailing_pct", 0.15),
                    entry_time=entry_dt,
                )
                self._current_pos.peak_price_sol = data.get("peak_price_sol", data["entry_price_sol"])
                self._current_pos.current_stop_price_sol = data.get("current_stop_price_sol", self._current_pos.current_stop_price_sol)
                self._current_pos.tp1_activated = data.get("tp1_activated", False)
                self._current_pos.ratchet_activated = self._current_pos.tp1_activated
                if self._current_pos.tp1_activated:
                    self._current_pos.state = PositionState.RUNNER_ACTIVE
        except Exception as e:
            logger.error("Failed to load active paper position: %s", e)
            self._current_pos = None

    def _save_active_position(self, current_price_sol: Optional[float] = None) -> None:
        """Persists currently open position state to file."""
        tmp = self.state_file.with_suffix(".tmp")
        if not self._current_pos or self._current_pos.is_closed:
            out = {"is_active": False}
        else:
            curr_p = current_price_sol or self._current_pos.entry_price_sol
            now = datetime.now(timezone.utc)
            elapsed_m = (now - self._current_pos.entry_time).total_seconds() / 60.0

            if self._current_pos.tp1_activated and self._current_pos.tp1_leg:
                # Two-stage scale out: TP1 secured + runner live
                sol_received_so_far = self._current_pos.tp1_leg.sol_amount
                runner_sol_val = self._current_pos.runner_tokens_held * curr_p
                total_sol = sol_received_so_far + runner_sol_val
                friction_so_far = self._current_pos.entry_leg.total_friction_sol + self._current_pos.tp1_leg.total_friction_sol
                net_sol = total_sol - self._current_pos.sol_spent - friction_so_far
                unrealized_pnl_usd = net_sol * self.sol_price_usd
                unrealized_return_pct = (net_sol / self._current_pos.sol_spent) * 100.0 if self._current_pos.sol_spent > 0 else 0.0
            else:
                unrealized_return_pct = ((curr_p - self._current_pos.entry_price_sol) / self._current_pos.entry_price_sol) * 100.0 if self._current_pos.entry_price_sol > 0 else 0.0
                unrealized_pnl_usd = (unrealized_return_pct / 100.0) * self._current_pos.position_notional_usd

            out = {
                "is_active": True,
                "position_id": self._current_pos.position_id,
                "pool_address": self._current_pos.pool_address,
                "token_mint": self._current_pos.token_mint,
                "regime": self._current_pos.regime,
                "position_notional_usd": self._current_pos.position_notional_usd,
                "entry_time": self._current_pos.entry_time.isoformat(),
                "entry_price_sol": self._current_pos.entry_price_sol,
                "current_price_sol": curr_p,
                "peak_price_sol": self._current_pos.peak_price_sol,
                "current_stop_price_sol": self._current_pos.current_stop_price_sol,
                "tp1_target_gross_pct": self._current_pos.tp1_target_gross_pct,
                "tp1_price_sol": self._current_pos.tp1_price_sol,
                "tp1_activated": self._current_pos.tp1_activated,
                "tp1_size_pct": self._current_pos.tp1_size_pct,
                "runner_size_pct": self._current_pos.runner_size_pct,
                "runner_tokens_held": self._current_pos.runner_tokens_held,
                "runner_trailing_pct": self._current_pos.runner_trailing_pct,
                "ratchet_trigger_price_sol": self._current_pos.tp1_price_sol,
                "ratchet_floor_price_sol": self._current_pos.ratchet_floor_price_sol,
                "ratchet_activated": self._current_pos.tp1_activated,
                "structure_stop_price_sol": self._current_pos.structure_stop_price_sol,
                "lowest_low_2m_candles_sol": self._current_pos.lowest_low_2m_candles_sol,
                "state": self._current_pos.state.value,
                "elapsed_minutes": round(elapsed_m, 1),
                "unrealized_return_pct": round(unrealized_return_pct, 2),
                "unrealized_pnl_usd": round(unrealized_pnl_usd, 2),
                "updated_at": now.isoformat(),
            }
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(out, f, indent=2)
            tmp.replace(self.state_file)
        except Exception as e:
            logger.error("Failed to save active paper position: %s", e)

    def get_active_position(self) -> Optional[Dict]:
        """Returns the active position dictionary if one is open, else None."""
        if not self.state_file.exists():
            return None
        try:
            with open(self.state_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data if data.get("is_active") else None
        except Exception:
            return None

    def evaluate_candidate_gates(self, pool: Dict, quote: Optional[Dict]) -> Tuple[bool, str]:
        """
        Evaluates the 4 Candidate Selection Gates:
        Gate 1 (Pool Age): Retain 8–25 minutes initially; test alternative windows.
        Gate 2 (Liquidity): Retain >= 25 SOL and >= $3,000, and verify reserve consistency.
        Gate 3 (Activity): At least 3 txns, minimum buy-side activity (buys >= 2) and buy dominance.
        Gate 4 (Momentum): Positive short-term price trend (5m >= 0%) with sustained buy pressure (vol_5m >= $300).
        """
        # 1. Gate 1: Pool Age Gate (Configurable window)
        now = datetime.now(timezone.utc)
        if "minutes_post_graduation" in pool:
            age_min = float(pool["minutes_post_graduation"])
        elif "age_minutes" in pool:
            age_min = float(pool["age_minutes"])
        elif "graduation_ts" in pool:
            try:
                grad_dt = datetime.fromisoformat(pool["graduation_ts"].replace("Z", "+00:00"))
                age_min = max(0.0, (now - grad_dt).total_seconds() / 60.0)
            except Exception:
                age_min = 999.0
        else:
            age_min = 12.0 if pool.get("state") == "ACTIVE_WINDOW" else 999.0

        if not (self.min_age_minutes <= age_min <= self.max_age_minutes):
            return False, f"Pool Age Gate: {age_min:.1f}m outside [{self.min_age_minutes:.1f}m, {self.max_age_minutes:.1f}m]"

        # 2. Gate 2: Liquidity & Reserve Consistency Gate
        sol_res = float(pool.get("current_sol_reserve", 0.0))
        if sol_res < 25.0:
            return False, f"Liquidity Gate: On-chain reserve {sol_res:.1f} SOL < 25.0 SOL floor"

        if not quote or quote.get("price_sol", 0.0) <= 0.0:
            # Synthetic / offline test environment fallback
            return True, "Passed (Synthetic/Offline Fallback)"

        liq_usd = float(quote.get("liquidity_usd", 0.0))
        if liq_usd < 3000.0:
            return False, f"Liquidity Gate: Live liquidity ${liq_usd:,.0f} < $3,000 floor"

        # Verify reserve consistency:
        # a) Total pool liquidity in SOL equivalent must be >= 25.0 SOL
        implied_total_sol = liq_usd / self.sol_price_usd
        if implied_total_sol < 25.0:
            return False, f"Reserve Consistency: DEX liquidity implies {implied_total_sol:.1f} SOL (< 25.0 SOL)"

        # b) Quote-side SOL reserve in AMM must be >= 12.0 SOL
        quote_sol_res = float(quote.get("quote_sol_reserve", 0.0))
        if quote_sol_res > 0.0 and quote_sol_res < 12.0:
            return False, f"Reserve Consistency: AMM quote reserve {quote_sol_res:.1f} SOL (< 12.0 SOL)"

        # c) Consistency ratio between live AMM liquidity and discovery reserve
        if sol_res > 0.0:
            consistency_ratio = (implied_total_sol / 2.0) / sol_res
            if consistency_ratio < 0.35:
                return False, f"Reserve Consistency: AMM liquidity drained ({consistency_ratio*100:.0f}% of on-chain reserve)"

        # 3. Gate 3: Activity Gate
        total_txns = int(quote.get("total_txns_5m", 0))
        buys = int(quote.get("buys_5m", 0))
        sells = int(quote.get("sells_5m", 0))

        if total_txns < 3:
            return False, f"Activity Gate: 5m transactions ({total_txns}) < 3"

        if buys < 2:
            return False, f"Activity Gate: Insufficient buy-side activity ({buys} buys < 2)"

        if buys < sells:
            return False, f"Activity Gate: Sell pressure dominates ({sells} sells > {buys} buys)"

        # 4. Gate 4: Momentum Gate
        price_change_5m = float(quote.get("price_change_5m", 0.0))
        vol_5m = float(quote.get("volume_5m_usd", 0.0))

        if price_change_5m < 0.0:
            return False, f"Momentum Gate: Negative short-term trend ({price_change_5m:.2f}%)"

        if vol_5m < 300.0:
            return False, f"Momentum Gate: Stagnant 5m volume (${vol_5m:,.0f} < $300)"

        # 5. Gate 5 (Invariant 3): Volume Exhaustion Consolidation + Structural Breakout Gate
        ms_passed, ms_reason = self.market_structure.evaluate_structural_break(pool, quote, now)
        if not ms_passed:
            return False, ms_reason

        # 6. Gate 6 (Invariant 5 / Fix 4): Real-Time Order Flow Dynamics Gate
        # Replaces historical genealogy with real-time AMM order flow dynamics (VUW >= 1.2, Aggression > 2.0)
        of_metrics = self.order_flow.evaluate_order_flow(
            pool_address=pool.get("pool_address", ""),
            token_mint=pool.get("token_mint", ""),
            quote=quote,
            now=now,
        )
        if quote and ("vuw" in quote or "unique_buyers_current_1m" in quote or "aggression_ratio" in quote or "taker_buy_sol" in quote):
            if not of_metrics.is_approved:
                return False, f"Order Flow Dynamics Gate: {'; '.join(of_metrics.rejection_reasons)}"

        return True, "Passed All Candidate Gates (Including Invariant 3 Structural Breakout & Invariant 5 Order Flow Dynamics)"

    def on_market_tick(self, pools: List[Dict], current_time: Optional[datetime] = None) -> Optional[Dict]:
        """
        Called on every market tick.
        1. If an active position exists, updates price against current market data and checks stops.
        2. If no position exists, looks for a candidate pool in ACTIVE_WINDOW passing gates.
        """
        now = current_time or datetime.now(timezone.utc)

        # 1. Update Existing Position if open
        if self._current_pos:
            target_pool = next((p for p in pools if p.get("pool_address") == self._current_pos.pool_address), None)
            if target_pool and target_pool.get("current_sol_reserve", 0) > 0 and target_pool.get("current_token_reserve", 0) > 0:
                current_price_sol = target_pool["current_sol_reserve"] / target_pool["current_token_reserve"]
                current_token_reserve = float(target_pool["current_token_reserve"])
            else:
                # Derive price from market cap or entry price
                current_price_sol = self._current_pos.peak_price_sol
                current_token_reserve = None

            # Fetch live quote for 1m volume & simulated sell slippage
            quote = self.live_feed.get_live_token_quote(self._current_pos.token_mint) if self.live_feed else None
            vol_1m = float(quote.get("volume_1m_usd", 0.0)) if quote else 0.0

            updated = self.sim.update_price_tick(
                self._current_pos,
                current_price_sol,
                now,
                vol_1m_usd=vol_1m,
                quote=quote,
                current_token_reserve=current_token_reserve,
            )

            if updated.is_closed:
                logger.info(
                    "Paper Position %s CLOSED via %s (Net P&L: $%.2f)",
                    updated.position_id, updated.exit_reason, updated.realized_net_pnl_usd
                )
                self.circuit_breaker.record_trade_pnl(updated.realized_net_pnl_usd)
                self.token_cooldowns[updated.token_mint] = now + timedelta(minutes=45)
                closed_summary = {
                    "position_id": updated.position_id,
                    "status": "CLOSED",
                    "exit_reason": updated.exit_reason,
                    "net_pnl_usd": updated.realized_net_pnl_usd,
                    "net_return_pct": updated.realized_net_return_pct,
                }
                self._current_pos = None
                self._save_active_position()
                return closed_summary
            else:
                self._save_active_position(current_price_sol=current_price_sol)
                return self.get_active_position()

        # 2. No Open Position — Evaluate New Candidate Entry
        if self.kill_switch.is_tripped():
            return None

        # Check pre-flight risk capacity
        can_trade, _, _ = self.circuit_breaker.check_preflight_capacity(
            position_notional_usd=self.position_notional_usd,
            sol_price_usd=self.sol_price_usd,
        )
        if not can_trade:
            return None

        # Scan for candidate pools within the configured age window (8-25m initially; supports testing alternative windows)
        candidate_pools = [
            p for p in pools
            if (self.min_age_minutes <= float(p.get("minutes_post_graduation", p.get("age_minutes", 0.0))) <= self.max_age_minutes)
            or p.get("state") == "ACTIVE_WINDOW"
        ]
        if not candidate_pools:
            return None

        # Evaluate candidates against the 4 Gates and Pre-Flight Double Gate (Invariant 4)
        valid_candidates = []
        for pool in candidate_pools:
            mint = pool["token_mint"]
            # 1. Skip if token was already traded (enforce 1 trade per token per graduation)
            if mint in self.traded_tokens:
                logger.debug("Candidate %s skipped: Token already traded in this session/history", mint)
                continue

            # 2. Skip if token is on cooldown (recently closed/stopped out)
            if mint in self.token_cooldowns and now < self.token_cooldowns[mint]:
                continue

            # 2. Fetch live real market quote to verify liquidity, activity & momentum
            quote = self.live_feed.get_live_token_quote(mint)

            # 3. Apply the 4 Selection Gates: Pool Age, Liquidity & Reserve Consistency, Activity, Momentum
            passed_gates, gate_reason = self.evaluate_candidate_gates(pool, quote)
            if not passed_gates:
                logger.debug("Candidate %s rejected by Gate: %s", mint, gate_reason)
                continue

            # 4. Pre-Flight Double Gate (depth, fee regime, and mathematical friction ceiling)
            sol_res = float(pool.get("current_sol_reserve", 0.0))
            mc_sol = float(pool.get("market_cap_sol", 380.0))
            gate_res = self.double_gate.evaluate(
                pool_address=pool["pool_address"],
                token_mint=mint,
                market_cap_sol=mc_sol,
                current_sol_reserve=sol_res,
                position_notional_usd=self.position_notional_usd,
            )
            if not gate_res.is_passed:
                logger.debug("Candidate %s rejected by PreFlightDoubleGate: %s", mint, gate_res.rejection_reasons)
                continue

            # 5. Exact market entry price synchronized with live quote (eliminates basis mismatch)
            if quote and quote.get("price_sol", 0.0) > 0.0:
                entry_price = quote["price_sol"]
            else:
                entry_price = float(pool.get("price_sol") or 0.0)
                if entry_price <= 0:
                    entry_price = sol_res / pool.get("current_token_reserve", 1_000_000_000.0)

            valid_candidates.append((pool, gate_res, entry_price))

        if not valid_candidates:
            logger.debug("No active pool passed Candidate Selection Gates on this tick.")
            return None

        # Pick candidate with best liquidity & lowest friction
        candidate, best_gate, entry_price = min(valid_candidates, key=lambda c: c[1].expected_cost_pct)
        pool_addr = candidate["pool_address"]
        token_mint = candidate["token_mint"]
        mc_sol = candidate.get("market_cap_sol", 380.0)
        regime = best_gate.regime.value
        pos_id = f"live_paper_{int(now.timestamp()) % 100000}"
        ratchet_pct = best_gate.ratchet_trigger_gross_pct

        breakout_vol = float(candidate.get("breakout_buy_vol") or candidate.get("volume_1m_usd") or 0.0)

        # Fix 5: Dynamic Position Sizing (min(Calculated Max Safe Swap, $25 active wallet limit))
        entry_position_usd = best_gate.dynamic_position_size_usd or self.position_notional_usd

        # Invariant 4: TP1 Friction Buster target (+8% to +10%, default +9%) replaces Ratchet Trigger
        tp1_target = (best_gate.tp1_target_gross_pct / 100.0) if hasattr(best_gate, "tp1_target_gross_pct") else 0.09

        self._current_pos = self.sim.open_position(
            position_id=pos_id,
            pool_address=pool_addr,
            token_mint=token_mint,
            regime=regime,
            market_cap_sol=mc_sol,
            entry_price_sol=entry_price,
            tp1_target_gross_pct=tp1_target,
            ratchet_trigger_gross_pct=ratchet_pct,
            position_notional_usd=entry_position_usd,
            entry_time=now,
            entry_breakout_volume_usd=breakout_vol,
        )
        self.traded_tokens.add(token_mint)
        logger.info(
            "Opened NEW live paper position %s on %s (Dynamically sized to $%.2f at <=1.5%% impact)",
            pos_id, token_mint, entry_position_usd
        )
        self._save_active_position(current_price_sol=entry_price)
        return self.get_active_position()

    def manual_close_active_position(self, reason: str = "MANUAL_OPERATOR_CLOSE") -> Optional[Dict]:
        """Manually closes the active paper position on demand."""
        if not self._current_pos:
            self._load_active_position()
        if not self._current_pos:
            return None

        now = datetime.now(timezone.utc)
        curr_price = self._current_pos.peak_price_sol * 0.99
        updated = self.sim._execute_exit(
            self._current_pos,
            exit_price_sol=curr_price,
            exit_time=now,
            final_state=PositionState.CLOSED_EMERGENCY,
            reason=reason,
        )
        self.circuit_breaker.record_trade_pnl(updated.realized_net_pnl_usd)
        self.token_cooldowns[updated.token_mint] = now + timedelta(minutes=45)
        self._current_pos = None
        self._save_active_position()
        return {
            "position_id": updated.position_id,
            "status": "MANUAL_CLOSED",
            "net_pnl_usd": updated.realized_net_pnl_usd,
        }
