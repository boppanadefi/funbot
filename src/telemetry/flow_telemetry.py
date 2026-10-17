"""
Flow Telemetry & Rolling Liquidity Metrics Engine.
Computes 10s, 30s, 60s rolling metrics, unique buyer flows,
executable depth absorption, and Path A+ friction models.
"""

from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
import logging
from typing import Deque, Dict, List, Optional
from src.telemetry.state_tracker import TrackedPool
from src.utils.config import ConfigLoader, FeeSchedule, TelemetryConfig

logger = logging.getLogger(__name__)


@dataclass
class SwapEvent:
    pool_address: str
    is_buy: bool
    sol_amount: float
    token_amount: float
    trader_pubkey: str
    timestamp: datetime
    slot: int


@dataclass
class RollingFlowMetrics:
    pool_address: str
    vol_10s_sol: float
    vol_30s_sol: float
    vol_60s_sol: float
    buys_60s: int
    sells_60s: int
    buy_ratio_60s: float
    unique_wallets_60s: int
    price_impact_20usd_pct: float
    modeled_friction_pct: float
    is_depth_acceptable: bool
    is_friction_acceptable: bool


class FlowTelemetryEngine:
    def __init__(
        self,
        fee_schedule: Optional[FeeSchedule] = None,
        telemetry_config: Optional[TelemetryConfig] = None,
    ):
        loader = ConfigLoader()
        self.fee_schedule = fee_schedule or loader.load_fee_schedule()
        self.telemetry_config = telemetry_config or loader.load_telemetry_config()
        self.swap_buffers: Dict[str, Deque[SwapEvent]] = {}

    def record_swap(self, event: SwapEvent, pool: TrackedPool) -> None:
        """Record swap event into buffer and update pool metrics."""
        if event.pool_address not in self.swap_buffers:
            self.swap_buffers[event.pool_address] = deque()

        buffer = self.swap_buffers[event.pool_address]
        buffer.append(event)

        # Update pool cumulative volume based on age
        age = pool.age_minutes
        if age < 5.0:
            pool.vol_0_to_5m += event.sol_amount
        elif 5.0 <= age < 8.0:
            pool.vol_5_to_8m += event.sol_amount
        elif 8.0 <= age <= 25.0:
            pool.vol_8_to_25m += event.sol_amount

        # Update trader tracking
        if event.is_buy:
            pool.unique_buyers.add(event.trader_pubkey)
            pool.current_sol_reserve += event.sol_amount
            pool.current_token_reserve -= event.token_amount
        else:
            pool.unique_sellers.add(event.trader_pubkey)
            pool.current_sol_reserve -= event.sol_amount
            pool.current_token_reserve += event.token_amount

        pool.total_swaps += 1

    def compute_rolling_metrics(self, pool: TrackedPool) -> RollingFlowMetrics:
        """Compute rolling 10s, 30s, 60s volume, depth impact, and friction."""
        now = datetime.now(timezone.utc)
        buffer = self.swap_buffers.get(pool.pool_address, deque())

        # Prune events older than 60 seconds
        cutoff = now - timedelta(seconds=60)
        while buffer and buffer[0].timestamp < cutoff:
            buffer.popleft()

        vol_10s = 0.0
        vol_30s = 0.0
        vol_60s = 0.0
        buys = 0
        sells = 0
        wallets = set()

        cutoff_10s = now - timedelta(seconds=10)
        cutoff_30s = now - timedelta(seconds=30)

        for s in buffer:
            vol_60s += s.sol_amount
            wallets.add(s.trader_pubkey)
            if s.is_buy:
                buys += 1
            else:
                sells += 1

            if s.timestamp >= cutoff_30s:
                vol_30s += s.sol_amount
            if s.timestamp >= cutoff_10s:
                vol_10s += s.sol_amount

        total_tx = buys + sells
        buy_ratio = (buys / total_tx) if total_tx > 0 else 0.5

        # Price impact for $20 notional test trade (SOL @ $155)
        sol_price = self.telemetry_config.sol_usd_reference_price
        test_size_sol = self.telemetry_config.simulated_test_notional_usd / sol_price
        sol_reserve = max(pool.current_sol_reserve, 1.0)
        impact_pct = (test_size_sol / (2.0 * sol_reserve)) * 100.0

        # Venue fee calculation (PumpSwap tiered fees)
        mc_sol = pool.market_cap_sol
        if mc_sol < 420.0:
            venue_fee = 1.250
        elif mc_sol < 1000.0:
            venue_fee = max(0.300, 1.200 - (mc_sol - 420.0) / 580.0 * 0.900)
        else:
            venue_fee = 0.300

        # Path A+ overhead: exactly 2 legs (entry + single exit)
        overhead_per_leg = (
            self.fee_schedule.base_signature_fee_sol
            + self.fee_schedule.priority_fee_cap_sol
            + self.fee_schedule.p50_jito_tip_sol
        )
        notional_sol = 15.0 / sol_price  # Target $15 position
        overhead_pct = (overhead_per_leg * 2.0) / notional_sol * 100.0

        # Total modeled friction = 2 * venue_fee + impact + overheads
        total_friction = (venue_fee * 2.0) + impact_pct + overhead_pct

        is_depth_ok = impact_pct < 2.0  # <2% impact for $20
        is_friction_ok = total_friction <= self.telemetry_config.max_acceptable_friction_pct  # <= 7.0%

        return RollingFlowMetrics(
            pool_address=pool.pool_address,
            vol_10s_sol=vol_10s,
            vol_30s_sol=vol_30s,
            vol_60s_sol=vol_60s,
            buys_60s=buys,
            sells_60s=sells,
            buy_ratio_60s=buy_ratio,
            unique_wallets_60s=len(wallets),
            price_impact_20usd_pct=impact_pct,
            modeled_friction_pct=total_friction,
            is_depth_acceptable=is_depth_ok,
            is_friction_acceptable=is_friction_ok,
        )
