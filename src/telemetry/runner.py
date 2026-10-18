"""
Telemetry Ingestion Daemon & Live Runner.
Supports both:
1. Real On-Chain Market Data Feed (GeckoTerminal & DexScreener APIs)
2. Synthetic Simulation Fallback Feed
Continuously executes Path A+ Paper Trading against incoming market data.
"""

import argparse
import asyncio
from datetime import datetime, timezone, timedelta
import logging
import random
import sys
import time
from typing import Optional

from src.engine.event_bus import EventBus, StreamEventType, LiveSwapEvent
from src.engine.staleness_breaker import DataStalenessCircuitBreaker
from src.engine.virtual_amm import VirtualAMMMirror
from src.telemetry.flow_telemetry import FlowTelemetryEngine, SwapEvent
from src.telemetry.pool_discovery import PoolDiscoveryEngine, PoolMigrationEvent, NATIVE_SOL_MINT
from src.telemetry.state_tracker import PoolStateTracker, PoolState
from src.telemetry.storage import TelemetryStorage
from src.telemetry.live_market_feed import LiveMarketFeed
from src.telemetry.ws_stream import PumpSwapWSSClient
from src.engine.live_paper_trader import LivePaperTradingEngine
from src.utils.config import ConfigLoader, TelemetryConfig

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("TelemetryRunner")


class TelemetryRunner:
    def __init__(self, config: Optional[TelemetryConfig] = None, mode: str = "real", *args, **kwargs):
        if "mode" in kwargs:
            mode = kwargs.pop("mode")
        loader = ConfigLoader()
        self.config = config or loader.load_telemetry_config()
        self.storage = TelemetryStorage(db_path=self.config.sqlite_db_path)
        self.discovery = PoolDiscoveryEngine(self.config)
        self.tracker = PoolStateTracker()
        self.flow_engine = FlowTelemetryEngine()
        self.virtual_amm = VirtualAMMMirror()
        self.staleness_breaker = DataStalenessCircuitBreaker(max_staleness_ms=1000.0)
        self.event_bus = EventBus()
        self.wss_client = PumpSwapWSSClient(
            wss_url=self.config.ws_rpc_url,
            event_bus=self.event_bus,
            staleness_breaker=self.staleness_breaker,
            virtual_amm=self.virtual_amm,
        )
        self.paper_engine = LivePaperTradingEngine(
            db_path=self.config.sqlite_db_path,
            virtual_amm=self.virtual_amm,
            staleness_breaker=self.staleness_breaker,
            event_bus=self.event_bus,
            enforce_staleness_breaker=(mode == "wss"),
        )
        self.live_feed = LiveMarketFeed(sol_price_usd=155.0)
        self.mode = mode

        # Connect callbacks
        self.discovery.register_callback(self._on_pool_discovered)

    def _on_pool_discovered(self, event: PoolMigrationEvent) -> None:
        tracked = self.tracker.on_pool_migration(event)
        self.storage.upsert_pool({
            "pool_address": tracked.pool_address,
            "token_mint": tracked.token_mint,
            "quote_mint": event.quote_mint,
            "is_canonical": event.is_canonical,
            "graduation_slot": event.graduation_slot,
            "graduation_ts": event.graduation_ts,
            "current_sol_reserve": tracked.current_sol_reserve,
            "market_cap_sol": tracked.market_cap_sol,
            "state": tracked.state.value,
        })
        # Mirror pool in Virtual AMM and subscribe to WSS account changes
        if not self.virtual_amm.has_pool(tracked.pool_address):
            self.virtual_amm.register_pool(
                pool_address=tracked.pool_address,
                token_mint=tracked.token_mint,
                initial_sol_reserve=tracked.current_sol_reserve,
                initial_token_reserve=tracked.current_token_reserve,
            )
        self.wss_client.subscribe_pool_accounts(tracked.pool_address)
        logger.info("Persisted & mirrored new pool: %s", tracked.pool_address)

    def fetch_real_market_tick(self) -> None:
        """Pulls real on-chain Solana tokens and executes real-data paper trading."""
        now = datetime.now(timezone.utc)
        real_pools = self.live_feed.fetch_newly_graduated_pools()

        if not real_pools:
            logger.info("No new real pools returned on this tick, will retry next tick.")
            if self.mode != "real":
                self.simulate_telemetry_tick()
            return

        for p in real_pools:
            self.storage.upsert_pool(p)
            self.storage.upsert_cliff_metrics({
                "pool_address": p["pool_address"],
                "vol_0_to_5m": round(p.get("volume_24h_usd", 0.0) * 0.1, 2),
                "vol_5_to_8m": round(p.get("volume_24h_usd", 0.0) * 0.02, 2),
                "vol_8_to_25m": round(p.get("volume_24h_usd", 0.0) * 0.35, 2),
                "cliff_decay_pct": -82.0,
                "recorded_ts": now.isoformat(),
            })

        # Check if an active paper trade is currently open
        active_pos = self.paper_engine.get_active_position()
        if active_pos and active_pos.get("token_mint"):
            mint = active_pos["token_mint"]
            # Fetch live real-time quote for the active position
            quote = self.live_feed.get_live_token_quote(mint)
            if quote and quote.get("price_sol", 0.0) > 0:
                current_price_sol = quote["price_sol"]
                logger.info("Live real price tick for active position %s (%s): %.8f SOL ($%.4f)",
                            active_pos["position_id"], quote.get("symbol", "TOKEN"), current_price_sol, quote.get("price_usd", 0.0))
                # Update tick against real price
                dummy_pool_repr = [{
                    "pool_address": active_pos["pool_address"],
                    "current_sol_reserve": current_price_sol * 206_900_000.0,
                    "current_token_reserve": 206_900_000.0,
                }]
                self.paper_engine.on_market_tick(dummy_pool_repr, now)
                return

        # Expire stale pools in DB (> 25m)
        self.storage.expire_stale_pools(max_age_minutes=self.paper_engine.max_age_minutes)

        # No active position open — evaluate newly graduated real pools in the active window
        active_candidates = self.storage.get_active_candidates(
            min_age_minutes=self.paper_engine.min_age_minutes,
            max_age_minutes=self.paper_engine.max_age_minutes,
        )
        if active_candidates:
            self.paper_engine.on_market_tick(active_candidates, now)

    def simulate_telemetry_tick(self) -> None:
        """Simulate realistic post-graduation events for testing and UI demonstration."""
        now = datetime.now(timezone.utc)

        # 1. Periodically discover a new pool (~15% chance per tick)
        if random.random() < 0.15:
            rand_hex = f"{random.randint(1000, 9999):x}"
            token_mint = f"Token{rand_hex.upper()}PumpFun{random.randint(100, 999)}"
            pool_addr = f"Pool{rand_hex}Swap{random.randint(100, 999)}"
            initial_reserve = random.uniform(80.0, 90.0)
            initial_tokens = 206_900_000.0
            mc_sol = (initial_reserve / initial_tokens) * 1_000_000_000.0

            age_bucket = random.choices(["fresh", "cliff", "secondary"], weights=[0.3, 0.2, 0.5])[0]
            if age_bucket == "fresh":
                age_delta = timedelta(minutes=random.uniform(0.5, 4.5))
            elif age_bucket == "cliff":
                age_delta = timedelta(minutes=random.uniform(5.2, 7.8))
            else:
                age_delta = timedelta(minutes=random.uniform(8.5, 23.5))

            event = PoolMigrationEvent(
                pool_address=pool_addr,
                token_mint=token_mint,
                quote_mint=NATIVE_SOL_MINT,
                is_canonical=True,
                graduation_slot=random.randint(300_000_000, 310_000_000),
                graduation_ts=(now - age_delta).isoformat(),
                initial_sol_reserve=initial_reserve,
                initial_token_reserve=initial_tokens,
                market_cap_sol=mc_sol,
            )
            self._on_pool_discovered(event)

        # 2. Update ticks and generate random swaps for existing pools
        for addr, pool in list(self.tracker.pools.items()):
            new_state = self.tracker.update_pool_tick(addr)

            is_buy = random.random() > 0.45
            swap_sol = random.uniform(0.2, 3.5)
            swap_tokens = swap_sol * (pool.current_token_reserve / pool.current_sol_reserve)

            swap = SwapEvent(
                pool_address=addr,
                is_buy=is_buy,
                sol_amount=swap_sol,
                token_amount=swap_tokens,
                trader_pubkey=f"Trader{random.randint(100, 999)}",
                timestamp=now,
                slot=random.randint(300_000_000, 310_000_000),
            )
            self.flow_engine.record_swap(swap, pool)
            metrics = self.flow_engine.compute_rolling_metrics(pool)

            # Recalculate local AMM reserves before strategy evaluation
            self.virtual_amm.process_swap_event(
                pool_address=addr,
                is_buy=is_buy,
                sol_amount=swap_sol,
                token_amount=swap_tokens,
                slot=swap.slot,
            )
            # Reset staleness breaker latency timer
            self.staleness_breaker.record_message_received(slot=swap.slot)
            # Publish swap event on Event Bus
            self.event_bus.publish(
                StreamEventType.SWAP,
                LiveSwapEvent(
                    pool_address=addr,
                    token_mint=pool.token_mint,
                    is_buy=is_buy,
                    sol_amount=swap_sol,
                    token_amount=swap_tokens,
                    trader_pubkey=swap.trader_pubkey,
                    slot=swap.slot,
                ),
            )

            self.storage.upsert_pool({
                "pool_address": pool.pool_address,
                "token_mint": pool.token_mint,
                "current_sol_reserve": round(pool.current_sol_reserve, 2),
                "market_cap_sol": round(pool.market_cap_sol, 1),
                "state": new_state.value,
                "graduation_ts": pool.graduation_ts.isoformat(),
                "price_impact_20usd_pct": round(metrics.price_impact_20usd_pct, 2),
                "modeled_friction_pct": round(metrics.modeled_friction_pct, 2),
                "last_updated_ts": now.isoformat(),
            })

            self.storage.upsert_cliff_metrics({
                "pool_address": pool.pool_address,
                "vol_0_to_5m": round(pool.vol_0_to_5m, 2),
                "vol_5_to_8m": round(pool.vol_5_to_8m, 2),
                "vol_8_to_25m": round(pool.vol_8_to_25m, 2),
                "cliff_decay_pct": round(pool.buyback_cliff_decay_pct, 1),
                "recorded_ts": now.isoformat(),
            })

        # 3. Continuous Live Paper Trading tick
        active_pools = self.storage.get_active_candidates()
        if active_pools:
            self.paper_engine.on_market_tick(active_pools, now)

    async def run_wss_stream(self) -> None:
        """Runs the live dedicated WebSocket stream (Helius/Triton) with paper trading."""
        logger.info("Starting Dedicated Solana WebSocket stream on %s", self.wss_client.wss_url)
        wss_task = asyncio.create_task(self.wss_client.connect_and_listen())

        try:
            while True:
                active_pools = self.storage.get_active_candidates()
                now = datetime.now(timezone.utc)
                if active_pools:
                    self.paper_engine.on_market_tick(active_pools, now)
                await asyncio.sleep(0.5)
        except asyncio.CancelledError:
            self.wss_client.stop()
            wss_task.cancel()

    def run_continuous(self, interval_seconds: float = 3.0, max_ticks: Optional[int] = None) -> None:
        """Run continuous observational telemetry ingestion."""
        logger.info("Starting TelemetryRunner in '%s' mode (Interval: %.1fs)...", self.mode, interval_seconds)
        if self.mode == "wss":
            try:
                asyncio.run(self.run_wss_stream())
            except KeyboardInterrupt:
                logger.info("TelemetryRunner WSS interrupted by operator.")
            return

        ticks = 0
        try:
            while max_ticks is None or ticks < max_ticks:
                if self.mode == "real":
                    try:
                        self.fetch_real_market_tick()
                    except Exception as e:
                        logger.warning("Live market tick encountered error: %s. Retrying next tick.", e)
                else:
                    self.simulate_telemetry_tick()

                ticks += 1
                time.sleep(interval_seconds)
        except KeyboardInterrupt:
            logger.info("TelemetryRunner interrupted by operator. Exiting cleanly.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Funbot Phase 1 Telemetry Runner")
    parser.add_argument("--interval", type=float, default=3.0, help="Tick interval in seconds")
    parser.add_argument("--ticks", type=int, default=None, help="Max ticks (for testing)")
    parser.add_argument("--mode", type=str, default="real", choices=["real", "sim", "wss"], help="Data feed mode ('real', 'sim', 'wss')")
    args = parser.parse_args()

    runner = TelemetryRunner(mode=args.mode)
    runner.run_continuous(interval_seconds=args.interval, max_ticks=args.ticks)
