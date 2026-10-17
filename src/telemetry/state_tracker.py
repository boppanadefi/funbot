"""
Pool Lifecycle & Stabilization State Tracker.
Enforces Invariant 1 (3–25m secondary momentum window) and
Invariant 3 (bypassing 0–3m protocol buyback cliff, requiring volume exhaustion consolidation + structural breakout).
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import logging
from typing import Dict, List, Optional
from src.telemetry.pool_discovery import PoolMigrationEvent
from src.utils.config import ConfigLoader, RiskLimits

logger = logging.getLogger(__name__)


class PoolState(Enum):
    MIGRATED = "MIGRATED"
    STABILIZING = "STABILIZING"           # 0 to 3 minutes (cooling period bypassing protocol buyback cliff)
    ACTIVE_WINDOW = "ACTIVE_WINDOW"       # 3 to 25 minutes (market structure & structural breakout evaluation zone)
    EXPIRED = "EXPIRED"                   # > 25 minutes (monitoring window closed)
    DROPPED = "DROPPED"                   # Rugged, zero volume, or abnormal failure


@dataclass
class TrackedPool:
    pool_address: str
    token_mint: str
    graduation_ts: datetime
    current_sol_reserve: float
    current_token_reserve: float
    market_cap_sol: float
    state: PoolState = PoolState.MIGRATED
    vol_0_to_5m: float = 0.0
    vol_5_to_8m: float = 0.0
    vol_8_to_25m: float = 0.0
    unique_buyers: set = field(default_factory=set)
    unique_sellers: set = field(default_factory=set)
    total_swaps: int = 0
    price_impact_20usd_pct: float = 0.0
    last_update_ts: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def age_minutes(self) -> float:
        now = datetime.now(timezone.utc)
        return (now - self.graduation_ts).total_seconds() / 60.0

    @property
    def buyback_cliff_decay_pct(self) -> float:
        """Percentage volume drop between 0-5m boost and 5-8m post-boost."""
        if self.vol_0_to_5m <= 0:
            return 0.0
        return max(0.0, (self.vol_0_to_5m - self.vol_5_to_8m) / self.vol_0_to_5m * 100.0)


class PoolStateTracker:
    def __init__(self, risk_limits: Optional[RiskLimits] = None):
        if risk_limits is None:
            loader = ConfigLoader()
            risk_limits = loader.load_risk_limits()
        self.risk_limits = risk_limits
        self.min_age_minutes = risk_limits.min_age_minutes      # 8.0 min
        self.max_age_minutes = risk_limits.max_age_minutes      # 25.0 min
        self.cliff_window = risk_limits.buyback_cliff_window    # [5.0, 8.0]
        self.pools: Dict[str, TrackedPool] = {}

    def on_pool_migration(self, event: PoolMigrationEvent) -> TrackedPool:
        """Initialize state tracking for newly discovered canonical pool."""
        try:
            grad_ts = datetime.fromisoformat(event.graduation_ts.replace("Z", "+00:00"))
        except Exception:
            grad_ts = datetime.now(timezone.utc)

        tracked = TrackedPool(
            pool_address=event.pool_address,
            token_mint=event.token_mint,
            graduation_ts=grad_ts,
            current_sol_reserve=event.initial_sol_reserve,
            current_token_reserve=event.initial_token_reserve,
            market_cap_sol=event.market_cap_sol,
            state=PoolState.STABILIZING,
        )
        self.pools[event.pool_address] = tracked
        logger.info("Initialized state tracking for %s (Age: 0.0m)", event.pool_address)
        return tracked

    def update_pool_tick(self, pool_address: str) -> PoolState:
        """Update pool state based on elapsed time and Invariant rules."""
        pool = self.pools.get(pool_address)
        if not pool:
            return PoolState.DROPPED

        age = pool.age_minutes
        pool.last_update_ts = datetime.now(timezone.utc)

        if age < self.min_age_minutes:
            # 0 to 3 min: Cooling window (Bypassing protocol cliff)
            pool.state = PoolState.STABILIZING
        elif self.min_age_minutes <= age <= self.max_age_minutes:
            # 3 to 25 min: Active candidate evaluation window
            pool.state = PoolState.ACTIVE_WINDOW
        else:
            # > 25 min: Expired
            pool.state = PoolState.EXPIRED

        return pool.state

    def get_active_candidates(self) -> List[TrackedPool]:
        """Return all pools currently within the valid 3–25 minute evaluation window."""
        active = []
        for addr, pool in self.pools.items():
            state = self.update_pool_tick(addr)
            if state == PoolState.ACTIVE_WINDOW:
                active.append(pool)
        return active
