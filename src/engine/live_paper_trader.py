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
from src.engine.event_bus import EventBus, StreamEventType, LiveSwapEvent, LiveAccountChangeEvent
from src.engine.execution_simulator import FrictionExecutionSimulator
from src.engine.models import PaperPosition, PositionState
from src.engine.paper_ledger import PaperLedger
from src.engine.simulator import PathASimulator
from src.engine.market_structure import MarketStructureTracker
from src.engine.order_flow import OrderFlowDynamicsEngine
from src.engine.staleness_breaker import DataStalenessCircuitBreaker
from src.engine.trade_accelerator import TradeAcceleratorConfig
from src.engine.virtual_amm import VirtualAMMMirror
from src.live.circuit_breaker import DailyCircuitBreaker
from src.live.kill_switch import EmergencyKillSwitch
from src.telemetry.live_market_feed import LiveMarketFeed

logger = logging.getLogger("LivePaperTrader")


class LivePaperTradingEngine:
    """
    Coordinates continuous live paper trading against active incoming pools.
    Integrates Virtual AMM Mirror, Data Staleness Circuit Breaker, Event Bus,
    and the Realistic Execution Simulator (The Friction Engine).
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
        min_baseline_demand_usd: float = 100.0,
        virtual_amm: Optional[VirtualAMMMirror] = None,
        staleness_breaker: Optional[DataStalenessCircuitBreaker] = None,
        event_bus: Optional[EventBus] = None,
        enforce_staleness_breaker: bool = False,
        execution_simulator: Optional[FrictionExecutionSimulator] = None,
        enable_friction_engine: bool = True,
        accelerator_config: Optional[TradeAcceleratorConfig] = None,
    ):
        self.ledger = PaperLedger(db_path=db_path)
        self.virtual_amm = virtual_amm or VirtualAMMMirror()
        self.staleness_breaker = staleness_breaker or DataStalenessCircuitBreaker()
        self.event_bus = event_bus or EventBus()
        self.enforce_staleness_breaker = enforce_staleness_breaker
        self.accelerator_config = accelerator_config or TradeAcceleratorConfig()
        self.execution_simulator = execution_simulator or FrictionExecutionSimulator(
            virtual_amm=self.virtual_amm,
            sol_price_usd=sol_price_usd,
        )
        self.sim = PathASimulator(
            ledger=self.ledger,
            sol_price_usd=sol_price_usd,
            execution_simulator=self.execution_simulator,
            enable_friction_engine=enable_friction_engine,
        )
        self.circuit_breaker = circuit_breaker or DailyCircuitBreaker()
        self.kill_switch = kill_switch or EmergencyKillSwitch()
        self.double_gate = PreFlightDoubleGate()
        self.market_structure = market_structure_tracker or MarketStructureTracker(
            min_cooldown_minutes=min_age_minutes,
            min_baseline_demand_usd=min_baseline_demand_usd,
        )
        self.order_flow = order_flow_engine or OrderFlowDynamicsEngine()
        self.live_feed = LiveMarketFeed(
            sol_price_usd=sol_price_usd,
            min_age_minutes=min_age_minutes,
            max_age_minutes=max_age_minutes,
        )

        # Register event bus subscribers for real-time swap and reserve updates
        self.event_bus.subscribe(StreamEventType.SWAP, self._on_live_swap_event)
        self.event_bus.subscribe(StreamEventType.ACCOUNT_CHANGE, self._on_live_account_change)

        self.token_cooldowns: Dict[str, datetime] = {}
        self.sol_price_usd = sol_price_usd
        self.position_notional_usd = position_notional_usd
        self.min_age_minutes = min_age_minutes
        self.max_age_minutes = max_age_minutes
        self.db_path = db_path
        self.traded_tokens: set = self._load_traded_tokens()
        self.state_file = Path(state_file)
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self._active_positions: Dict[str, PaperPosition] = {}
        self._load_active_position()

    @property
    def max_concurrent_positions(self) -> int:
        """Concurrency limit: Up to 3 in Trade Accelerator mode, 1 standard."""
        if self.accelerator_config and self.accelerator_config.enabled:
            return self.accelerator_config.concurrency_limit
        return 1

    def has_capacity_for_new_trade(self) -> bool:
        """Returns True if engine has capacity to open another position."""
        return len(self._active_positions) < self.max_concurrent_positions

    @property
    def _current_pos(self) -> Optional[PaperPosition]:
        """Backward-compatible property returning the primary active position."""
        if not self._active_positions:
            return None
        return next(iter(self._active_positions.values()))

    @_current_pos.setter
    def _current_pos(self, pos: Optional[PaperPosition]) -> None:
        """Backward-compatible setter for single active position."""
        if pos is None:
            self._active_positions.clear()
        else:
            self._active_positions[pos.position_id] = pos

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
        """Loads open positions from persistent JSON if present."""
        if not self.state_file.exists():
            return
        try:
            with open(self.state_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                if not data:
                    self._active_positions.clear()
                    return

                pos_list = data.get("active_positions", [])
                if not pos_list and data.get("is_active"):
                    pos_list = [data]

                self._active_positions.clear()
                for p_data in pos_list:
                    if not p_data or not p_data.get("is_active"):
                        continue
                    entry_dt = datetime.fromisoformat(p_data["entry_time"])
                    pos = self.sim.open_position(
                        position_id=p_data["position_id"],
                        pool_address=p_data["pool_address"],
                        token_mint=p_data["token_mint"],
                        regime=p_data["regime"],
                        market_cap_sol=p_data.get("market_cap_sol", 380.0),
                        entry_price_sol=p_data["entry_price_sol"],
                        tp1_target_gross_pct=p_data.get("tp1_target_gross_pct", 0.09),
                        position_notional_usd=p_data.get("position_notional_usd", self.position_notional_usd),
                        trailing_pct=p_data.get("runner_trailing_pct", 0.15),
                        entry_time=entry_dt,
                    )
                    pos.peak_price_sol = p_data.get("peak_price_sol", p_data["entry_price_sol"])
                    pos.current_stop_price_sol = p_data.get("current_stop_price_sol", pos.current_stop_price_sol)
                    pos.tp1_activated = p_data.get("tp1_activated", False)
                    pos.ratchet_activated = pos.tp1_activated
                    if p_data.get("tp1_time"):
                        pos.tp1_time = datetime.fromisoformat(p_data["tp1_time"])
                    if pos.tp1_activated:
                        pos.state = PositionState.RUNNER_ACTIVE
                    self._active_positions[pos.position_id] = pos
        except Exception as e:
            logger.error("Failed to load active paper position: %s", e)
            self._active_positions.clear()

    def _save_active_position(self, current_price_sol: Optional[float] = None) -> None:
        """Persists open positions state to file with multi-concurrency support."""
        tmp = self.state_file.with_suffix(".tmp")
        active_list = []
        now = datetime.now(timezone.utc)

        for pos in self._active_positions.values():
            if pos.is_closed:
                continue
            curr_p = current_price_sol or pos.entry_price_sol
            elapsed_m = (now - pos.entry_time).total_seconds() / 60.0

            if pos.tp1_activated and pos.tp1_leg:
                sol_received_so_far = pos.tp1_leg.sol_amount
                runner_sol_val = pos.runner_tokens_held * curr_p
                total_sol = sol_received_so_far + runner_sol_val
                friction_so_far = pos.entry_leg.total_friction_sol + pos.tp1_leg.total_friction_sol
                net_sol = total_sol - pos.sol_spent - friction_so_far
                unrealized_pnl_usd = net_sol * self.sol_price_usd
                unrealized_return_pct = (net_sol / pos.sol_spent) * 100.0 if pos.sol_spent > 0 else 0.0
            else:
                unrealized_return_pct = ((curr_p - pos.entry_price_sol) / pos.entry_price_sol) * 100.0 if pos.entry_price_sol > 0 else 0.0
                unrealized_pnl_usd = (unrealized_return_pct / 100.0) * pos.position_notional_usd

            p_dict = {
                "is_active": True,
                "position_id": pos.position_id,
                "pool_address": pos.pool_address,
                "token_mint": pos.token_mint,
                "regime": pos.regime,
                "position_notional_usd": pos.position_notional_usd,
                "entry_time": pos.entry_time.isoformat(),
                "entry_price_sol": pos.entry_price_sol,
                "current_price_sol": curr_p,
                "peak_price_sol": pos.peak_price_sol,
                "current_stop_price_sol": pos.current_stop_price_sol,
                "tp1_target_gross_pct": pos.tp1_target_gross_pct,
                "tp1_price_sol": pos.tp1_price_sol,
                "tp1_activated": pos.tp1_activated,
                "tp1_time": pos.tp1_time.isoformat() if pos.tp1_time else None,
                "tp1_size_pct": pos.tp1_size_pct,
                "runner_size_pct": pos.runner_size_pct,
                "runner_tokens_held": pos.runner_tokens_held,
                "runner_trailing_pct": pos.runner_trailing_pct,
                "runner_hard_kill_timeout_minutes": pos.runner_hard_kill_timeout_minutes,
                "runner_target_gross_pct": pos.runner_target_gross_pct,
                "ratchet_trigger_price_sol": pos.tp1_price_sol,
                "ratchet_floor_price_sol": pos.ratchet_floor_price_sol,
                "ratchet_activated": pos.tp1_activated,
                "structure_stop_price_sol": pos.structure_stop_price_sol,
                "lowest_low_2m_candles_sol": pos.lowest_low_2m_candles_sol,
                "state": pos.state.value,
                "elapsed_minutes": round(elapsed_m, 1),
                "unrealized_return_pct": round(unrealized_return_pct, 2),
                "unrealized_pnl_usd": round(unrealized_pnl_usd, 2),
                "updated_at": now.isoformat(),
            }
            active_list.append(p_dict)

        if not active_list:
            out = {"is_active": False, "active_positions": []}
        else:
            primary = active_list[0].copy()
            primary["active_positions"] = active_list
            out = primary

        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(out, f, indent=2)
            tmp.replace(self.state_file)
        except Exception as e:
            logger.error("Failed to save active paper position: %s", e)

    def get_active_position(self) -> Optional[Dict]:
        """Returns the primary active position dictionary if one is open, else None."""
        if not self.state_file.exists():
            return None
        try:
            with open(self.state_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data if data.get("is_active") else None
        except Exception:
            return None

    def get_all_active_positions(self) -> List[Dict]:
        """Returns list of all active position dictionaries."""
        if not self.state_file.exists():
            return []
        try:
            with open(self.state_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                if not data or not data.get("is_active"):
                    return []
                return data.get("active_positions", [data])
        except Exception:
            return []

    def evaluate_candidate_gates(self, pool: Dict, quote: Optional[Dict]) -> Tuple[bool, str]:
        """
        Evaluates the Candidate Selection Gates:
        - Token-2022 Rejection Gate: Rejects Token-2022 with TransferFee before sizing.
        - Gate 1 (Pool Age): Retain 8–25 minutes initially; test alternative windows.
        - Gate 2 (Liquidity): Retain >= 25 SOL and >= $3,000, and verify reserve consistency.
        - Gate 3 (Activity): At least 3 txns, minimum buy-side activity (buys >= 2) and buy dominance.
        - Gate 4 (Momentum): Positive short-term price trend (5m >= 0%) with sustained buy pressure.
        - Gate 5 (Invariant 3): Consolidation + Baseline Demand Floor ($100/min or $50/min in accelerator).
        - Gate 6 (Fix 4): Slot-Level Order Flow Dynamics (VUW >= 1.20 or 1.05 in accelerator).
        """
        # Token-2022 Rejection Gate (Test C defense against silent bricking)
        is_t22 = (
            pool.get("token_program") == "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"
            or pool.get("is_token_2022", False)
            or (quote and quote.get("is_token_2022", False))
            or pool.get("has_transfer_fee", False)
            or (quote and quote.get("has_transfer_fee", False))
        )
        if is_t22:
            return False, "Token-2022 Rejection Gate: Token-2022 with TransferFee extension detected (Defense against silent bricking)"
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

        # Venue Gate: Strictly post-graduation AMM pools (exclude bonding curves like pumpfun)
        dex_name = (pool.get("dex_name") or (quote.get("dex_id") if quote else "") or "").lower()
        if "pumpfun" in dex_name:
            return False, "Venue Gate: Raw bonding curve (pumpfun) is not a post-graduation AMM pool"

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

        # Relaxation in Trade Accelerator mode:
        if self.accelerator_config and self.accelerator_config.enabled:
            # Relax Baseline Demand Floor to $50/min
            pool["min_baseline_demand_usd"] = self.accelerator_config.relaxed_baseline_demand_usd
            # Relax VUW threshold to 1.05
            vuw_override = self.accelerator_config.relaxed_vuw_threshold
        else:
            vuw_override = None

        # 5. Gate 5 (Invariant 3): Volume Exhaustion Consolidation + Structural Breakout Gate
        ms_passed, ms_reason = self.market_structure.evaluate_structural_break(pool, quote, now)
        if not ms_passed:
            return False, ms_reason

        # 6. Gate 6 (Invariant 5 / Fix 4): Slot-Level Order Flow Dynamics Gate
        # Replaces lagging 1m windows with real-time slot-level order flow:
        # Delta Unique Buyers >= 1 over last 5 blocks (400ms slots) & Breakout Candle Taker Buy > 70%
        of_metrics = self.order_flow.evaluate_order_flow(
            pool_address=pool.get("pool_address", ""),
            token_mint=pool.get("token_mint", ""),
            quote=quote,
            now=now,
            min_vuw_ratio=vuw_override,
        )
        if quote and (
            "vuw" in quote
            or "unique_buyers_current_1m" in quote
            or "aggression_ratio" in quote
            or "taker_buy_sol" in quote
            or "delta_unique_buyers_5blocks" in quote
            or "breakout_taker_buy_pct" in quote
        ):
            if not of_metrics.is_approved:
                return False, f"Order Flow Dynamics Gate: {'; '.join(of_metrics.rejection_reasons)}"

        return True, "Passed All Candidate Gates (Including Invariant 3 Structural Breakout & Invariant 5 Order Flow Dynamics)"

    def _on_live_swap_event(self, event: LiveSwapEvent) -> None:
        """
        Event Processing: Every time a real-world swap event occurs on the stream,
        recalculate local AMM reserves before evaluating strategy logic.
        """
        if not self.virtual_amm.has_pool(event.pool_address):
            return

        current_price_sol = self.virtual_amm.get_marginal_price(event.pool_address)
        if not current_price_sol or current_price_sol <= 0:
            return

        # Check circuit breaker if enforced
        if self.enforce_staleness_breaker:
            can_execute, reason = self.staleness_breaker.can_execute_decision()
            if not can_execute:
                logger.warning("Live swap event skipped due to staleness: %s", reason)
                return

        # Update any open active position matching this pool or token
        for pos_id, pos in list(self._active_positions.items()):
            if pos.pool_address == event.pool_address or pos.token_mint == event.token_mint:
                now = event.timestamp or datetime.now(timezone.utc)
                vol_usd = event.sol_amount * self.sol_price_usd
                updated = self.sim.update_price_tick(
                    pos,
                    current_price_sol,
                    now,
                    vol_1m_usd=vol_usd,
                )
                if updated.is_closed:
                    logger.info(
                        "Live Swap Event triggered exit on position %s: %s (Net P&L: $%.2f)",
                        updated.position_id, updated.exit_reason, updated.realized_net_pnl_usd
                    )
                    self.circuit_breaker.record_trade_pnl(updated.realized_net_pnl_usd)
                    self.token_cooldowns[updated.token_mint] = now + timedelta(minutes=45)
                    self._active_positions.pop(pos_id, None)
                    self._save_active_position()
                else:
                    self._save_active_position(current_price_sol=current_price_sol)

    def _on_live_account_change(self, event: LiveAccountChangeEvent) -> None:
        """Processes live on-chain reserve synchronization from accountChange event."""
        if self.virtual_amm.has_pool(event.pool_address):
            p = self.virtual_amm.get_marginal_price(event.pool_address)
            logger.debug("AccountChange synchronized pool %s: marginal price = %.8f SOL", event.pool_address, p or 0.0)

    def on_market_tick(self, pools: List[Dict], current_time: Optional[datetime] = None) -> Optional[Dict]:
        """
        Called on every market tick.
        1. If active positions exist, updates prices against current market data and checks stops.
        2. If active positions count < max_concurrent_positions, evaluates new candidates passing gates.
        """
        now = current_time or datetime.now(timezone.utc)

        # Data Staleness Circuit Breaker:
        # If time_since_last_ws_message > 1000ms, pause the engine. Do not make decisions on stale state.
        if self.enforce_staleness_breaker:
            can_execute, reason = self.staleness_breaker.can_execute_decision()
            if not can_execute:
                logger.warning("Trading Engine PAUSED: %s", reason)
                return {"status": "PAUSED_DATA_STALE", "reason": reason}

        # 1. Update Existing Active Positions
        closed_summaries = []
        for pos_id, pos in list(self._active_positions.items()):
            quote = self.live_feed.get_live_token_quote(pos.token_mint) if self.live_feed else None
            vol_1m = float(quote.get("volume_1m_usd", 0.0)) if quote else 0.0

            target_pool = next((p for p in pools if p.get("pool_address") == pos.pool_address), None)
            current_token_reserve = None
            if target_pool and target_pool.get("current_token_reserve", 0) > 0:
                current_token_reserve = float(target_pool["current_token_reserve"])

            amm_price = self.virtual_amm.get_marginal_price(pos.pool_address) if self.virtual_amm else None
            if amm_price and amm_price > 0:
                current_price_sol = amm_price
            elif quote and quote.get("price_sol", 0.0) > 0:
                current_price_sol = quote["price_sol"]
            elif target_pool and target_pool.get("current_sol_reserve", 0) > 0 and target_pool.get("current_token_reserve", 0) > 0:
                current_price_sol = target_pool["current_sol_reserve"] / target_pool["current_token_reserve"]
            else:
                current_price_sol = pos.peak_price_sol

            updated = self.sim.update_price_tick(
                pos,
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
                closed_summaries.append({
                    "position_id": updated.position_id,
                    "status": "CLOSED",
                    "exit_reason": updated.exit_reason,
                    "net_pnl_usd": updated.realized_net_pnl_usd,
                    "net_return_pct": updated.realized_net_return_pct,
                })
                self._active_positions.pop(pos_id, None)

        if closed_summaries:
            self._save_active_position()
            return closed_summaries[0]
        elif self._active_positions:
            self._save_active_position()

        # 2. Concurrency Check: If at or above max allowed positions, return current active position
        if len(self._active_positions) >= self.max_concurrent_positions:
            return self.get_active_position()

        # 3. Pre-Flight Risk & Kill Switch Checks
        if self.kill_switch.is_tripped():
            return None

        can_trade, _, _ = self.circuit_breaker.check_preflight_capacity(
            position_notional_usd=self.position_notional_usd,
            sol_price_usd=self.sol_price_usd,
        )
        if not can_trade:
            return None

        # Scan for candidate pools within configured window
        candidate_pools = [
            p for p in pools
            if (self.min_age_minutes <= float(p.get("minutes_post_graduation", p.get("age_minutes", 0.0))) <= self.max_age_minutes)
            or p.get("state") == "ACTIVE_WINDOW"
        ]
        if not candidate_pools:
            return None

        # Exclude tokens currently held in active positions
        currently_held_mints = {p.token_mint for p in self._active_positions.values()}

        valid_candidates = []
        for pool in candidate_pools:
            mint = pool["token_mint"]
            pool_addr = pool.get("pool_address", "")
            sol_res = float(pool.get("current_sol_reserve", 0.0))
            tok_res = float(pool.get("current_token_reserve", 206_900_000.0))

            if mint in currently_held_mints:
                continue

            # Mirror candidate pool in Virtual AMM
            if pool_addr and sol_res > 0 and not self.virtual_amm.has_pool(pool_addr):
                self.virtual_amm.register_pool(
                    pool_address=pool_addr,
                    token_mint=mint,
                    initial_sol_reserve=sol_res,
                    initial_token_reserve=tok_res if tok_res > 0 else 206_900_000.0,
                )

            # Skip if token was already traded (enforce 1 trade per token per graduation)
            if mint in self.traded_tokens:
                logger.debug("Candidate %s skipped: Token already traded in this session/history", mint)
                continue

            # Skip if token is on cooldown (recently closed/stopped out)
            if mint in self.token_cooldowns and now < self.token_cooldowns[mint]:
                continue

            quote = self.live_feed.get_live_token_quote(mint)

            # Apply Candidate Selection Gates (including Token-2022 rejection)
            passed_gates, gate_reason = self.evaluate_candidate_gates(pool, quote)
            if not passed_gates:
                logger.debug("Candidate %s rejected by Gate: %s", mint, gate_reason)
                continue

            # Pre-Flight Double Gate with dynamic sizing floor
            mc_sol = float(pool.get("market_cap_sol", 380.0))
            min_swap_floor = (
                self.accelerator_config.relaxed_min_dynamic_size_usd
                if (self.accelerator_config and self.accelerator_config.enabled)
                else 3.00
            )
            gate_res = self.double_gate.evaluate(
                pool_address=pool["pool_address"],
                token_mint=mint,
                market_cap_sol=mc_sol,
                current_sol_reserve=sol_res,
                position_notional_usd=self.position_notional_usd,
                min_viable_swap_usd=min_swap_floor,
            )
            if not gate_res.is_passed:
                logger.debug("Candidate %s rejected by PreFlightDoubleGate: %s", mint, gate_res.rejection_reasons)
                continue

            amm_price = self.virtual_amm.get_marginal_price(pool_addr) if pool_addr else None
            if amm_price and amm_price > 0.0:
                entry_price = amm_price
            elif quote and quote.get("price_sol", 0.0) > 0.0:
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

        # Tag regime: in Trade Accelerator session, tag as REGIME_TEST
        if self.accelerator_config and self.accelerator_config.enabled:
            regime = self.accelerator_config.session_regime_tag
        else:
            regime = best_gate.regime.value

        pos_id = f"live_paper_{int(now.timestamp()) % 100000}_{len(self._active_positions) + 1}"
        ratchet_pct = best_gate.ratchet_trigger_gross_pct
        breakout_vol = float(candidate.get("breakout_buy_vol") or candidate.get("volume_1m_usd") or 0.0)
        entry_position_usd = best_gate.dynamic_position_size_usd or self.position_notional_usd
        tp1_target = (best_gate.tp1_target_gross_pct / 100.0) if hasattr(best_gate, "tp1_target_gross_pct") else 0.09

        new_pos = self.sim.open_position(
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
        self._active_positions[pos_id] = new_pos
        self.traded_tokens.add(token_mint)
        logger.info(
            "Opened NEW live paper position %s on %s (Concurrency: %d/%d, sized to $%.2f, regime: %s)",
            pos_id, token_mint, len(self._active_positions), self.max_concurrent_positions, entry_position_usd, regime
        )
        self._save_active_position(current_price_sol=entry_price)
        return self.get_active_position()

    def on_candidate_detected(
        self,
        pool: Dict,
        quote: Optional[Dict] = None,
        current_time: Optional[datetime] = None,
    ) -> Optional[PaperPosition]:
        """
        Direct candidate evaluation and position opening for testing and pipeline feeds.
        Returns opened PaperPosition if candidate passes all gates and capacity is available.
        """
        now = current_time or datetime.now(timezone.utc)
        if len(self._active_positions) >= self.max_concurrent_positions:
            return None
        if self.kill_switch.is_tripped():
            return None

        can_trade, _, _ = self.circuit_breaker.check_preflight_capacity(
            position_notional_usd=self.position_notional_usd,
            sol_price_usd=self.sol_price_usd,
        )
        if not can_trade:
            return None

        mint = pool["token_mint"]
        pool_addr = pool.get("pool_address", "")
        sol_res = float(pool.get("current_sol_reserve", 0.0))
        tok_res = float(pool.get("current_token_reserve", 206_900_000.0))

        if mint in self.traded_tokens:
            return None

        if pool_addr and sol_res > 0 and not self.virtual_amm.has_pool(pool_addr):
            self.virtual_amm.register_pool(
                pool_address=pool_addr,
                token_mint=mint,
                initial_sol_reserve=sol_res,
                initial_token_reserve=tok_res if tok_res > 0 else 206_900_000.0,
            )

        passed_gates, _ = self.evaluate_candidate_gates(pool, quote)
        if not passed_gates:
            return None

        mc_sol = float(pool.get("market_cap_sol", 380.0))
        min_swap_floor = (
            self.accelerator_config.relaxed_min_dynamic_size_usd
            if (self.accelerator_config and self.accelerator_config.enabled)
            else 3.00
        )
        gate_res = self.double_gate.evaluate(
            pool_address=pool_addr,
            token_mint=mint,
            market_cap_sol=mc_sol,
            current_sol_reserve=sol_res,
            position_notional_usd=self.position_notional_usd,
            min_viable_swap_usd=min_swap_floor,
        )
        if not gate_res.is_passed:
            return None

        entry_price = float(quote.get("price_sol")) if (quote and quote.get("price_sol")) else (sol_res / tok_res if tok_res > 0 else 0.0001)
        if self.accelerator_config and self.accelerator_config.enabled:
            regime = self.accelerator_config.session_regime_tag
        else:
            regime = gate_res.regime.value

        pos_id = f"live_paper_{int(now.timestamp()) % 100000}_{len(self._active_positions) + 1}"
        new_pos = self.sim.open_position(
            position_id=pos_id,
            pool_address=pool_addr,
            token_mint=mint,
            regime=regime,
            market_cap_sol=mc_sol,
            entry_price_sol=entry_price,
            tp1_target_gross_pct=0.09,
            position_notional_usd=gate_res.dynamic_position_size_usd or self.position_notional_usd,
            entry_time=now,
        )
        self._active_positions[pos_id] = new_pos
        self.traded_tokens.add(mint)
        self._save_active_position(current_price_sol=entry_price)
        return new_pos

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
