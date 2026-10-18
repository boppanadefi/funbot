"""
Virtual AMM Mirror for Solana PumpSwap / Constant-Product Pools.
Enforces exact real-time pool reserve simulation (x * y = k) instead of relying on lagging candle closes.
Recalculates exact reserves on every real-world swap event and synchronizes with accountChange balance events.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
import logging
from typing import Dict, Optional, Tuple

logger = logging.getLogger("VirtualAMM")


@dataclass
class SwapQuote:
    """Exact quote returned by Virtual AMM execution simulation."""
    is_buy: bool
    sol_amount_in: float
    token_amount_out: float
    sol_amount_out: float
    venue_fee_sol: float
    effective_price_sol: float
    marginal_price_before: float
    marginal_price_after: float
    price_impact_pct: float
    new_sol_reserve: float
    new_token_reserve: float


@dataclass
class VirtualPool:
    """
    Maintains exact in-memory mirrored state of a candidate liquidity pool:
    {"pool_address": {"sol_reserve": X, "token_reserve": Y, "k": X * Y}}
    """
    pool_address: str
    token_mint: str
    sol_reserve: float
    token_reserve: float
    k: float = field(init=False)
    venue_fee_pct: float = 1.25  # 1.25% default canonical PumpSwap fee
    last_update_time: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_slot: int = 0
    cumulative_swaps: int = 0
    cumulative_sol_volume: float = 0.0
    cumulative_buy_sol: float = 0.0
    cumulative_sell_sol: float = 0.0
    last_swap_is_buy: Optional[bool] = None
    last_swap_sol: float = 0.0

    def __post_init__(self):
        if self.sol_reserve <= 0:
            raise ValueError(f"sol_reserve must be > 0, got {self.sol_reserve}")
        if self.token_reserve <= 0:
            raise ValueError(f"token_reserve must be > 0, got {self.token_reserve}")
        self.k = self.sol_reserve * self.token_reserve

    @property
    def marginal_price_sol(self) -> float:
        """Current instantaneous marginal price: sol_reserve / token_reserve."""
        return self.sol_reserve / self.token_reserve if self.token_reserve > 0 else 0.0

    @property
    def pool_state_dict(self) -> Dict:
        """Dictionary representation matching {"sol_reserve": X, "token_reserve": Y, "k": X*Y}."""
        return {
            "sol_reserve": round(self.sol_reserve, 6),
            "token_reserve": round(self.token_reserve, 4),
            "k": round(self.k, 4),
            "marginal_price_sol": self.marginal_price_sol,
            "last_slot": self.last_slot,
            "last_update_time": self.last_update_time.isoformat(),
        }

    def quote_swap(self, sol_in: float, is_buy: bool) -> SwapQuote:
        """
        Simulates constant-product swap execution without mutating internal reserves.
        Formula: (x + delta_x_net) * (y - delta_y) = k
        """
        fee_rate = self.venue_fee_pct / 100.0
        p_before = self.marginal_price_sol

        if is_buy:
            # Buyer pays SOL in, receives tokens out
            fee_sol = sol_in * fee_rate
            net_sol_in = sol_in - fee_sol
            new_sol_res = self.sol_reserve + sol_in
            new_token_res = self.k / (self.sol_reserve + net_sol_in)
            tokens_out = max(0.0, self.token_reserve - new_token_res)
            effective_p = sol_in / tokens_out if tokens_out > 0 else p_before
            p_after = new_sol_res / new_token_res if new_token_res > 0 else p_before
            impact_pct = max(0.0, ((effective_p - p_before) / p_before) * 100.0) if p_before > 0 else 0.0

            return SwapQuote(
                is_buy=True,
                sol_amount_in=sol_in,
                token_amount_out=tokens_out,
                sol_amount_out=0.0,
                venue_fee_sol=fee_sol,
                effective_price_sol=effective_p,
                marginal_price_before=p_before,
                marginal_price_after=p_after,
                price_impact_pct=impact_pct,
                new_sol_reserve=new_sol_res,
                new_token_reserve=new_token_res,
            )
        else:
            # Seller sells tokens in, receives SOL out
            # sol_in here represents tokens to sell if called with token quantity
            tokens_in = sol_in
            new_token_res = self.token_reserve + tokens_in
            new_sol_res_gross = self.k / new_token_res
            gross_sol_out = max(0.0, self.sol_reserve - new_sol_res_gross)
            fee_sol = gross_sol_out * fee_rate
            net_sol_out = gross_sol_out - fee_sol
            new_sol_res = self.sol_reserve - gross_sol_out
            effective_p = gross_sol_out / tokens_in if tokens_in > 0 else p_before
            p_after = new_sol_res / new_token_res if new_token_res > 0 else p_before
            impact_pct = max(0.0, ((p_before - effective_p) / p_before) * 100.0) if p_before > 0 else 0.0

            return SwapQuote(
                is_buy=False,
                sol_amount_in=0.0,
                token_amount_out=0.0,
                sol_amount_out=net_sol_out,
                venue_fee_sol=fee_sol,
                effective_price_sol=effective_p,
                marginal_price_before=p_before,
                marginal_price_after=p_after,
                price_impact_pct=impact_pct,
                new_sol_reserve=new_sol_res,
                new_token_reserve=new_token_res,
            )

    def apply_swap(
        self,
        is_buy: bool,
        sol_amount: float,
        token_amount: Optional[float] = None,
        slot: int = 0,
        timestamp: Optional[datetime] = None,
    ) -> float:
        """
        Recalculates local AMM reserves immediately upon observing a swap on the live stream.
        Returns the new marginal price in SOL.
        """
        now = timestamp or datetime.now(timezone.utc)
        fee_rate = self.venue_fee_pct / 100.0

        if is_buy:
            # Real-world buy: SOL added to curve, tokens deducted
            fee_sol = sol_amount * fee_rate
            net_sol_in = sol_amount - fee_sol
            if token_amount and token_amount > 0:
                self.sol_reserve += sol_amount
                self.token_reserve = max(1.0, self.token_reserve - token_amount)
            else:
                new_token_res = self.k / (self.sol_reserve + net_sol_in)
                self.token_reserve = max(1.0, new_token_res)
                self.sol_reserve += sol_amount

            self.cumulative_buy_sol += sol_amount
        else:
            # Real-world sell: tokens added to curve, SOL deducted
            if token_amount and token_amount > 0:
                self.token_reserve += token_amount
                self.sol_reserve = max(0.01, self.sol_reserve - sol_amount)
            else:
                gross_sol_out = sol_amount / (1.0 - fee_rate)
                new_token_res = self.k / max(0.01, self.sol_reserve - gross_sol_out)
                self.sol_reserve = max(0.01, self.sol_reserve - gross_sol_out)
                self.token_reserve = new_token_res

            self.cumulative_sell_sol += sol_amount

        # Update Constant Product Invariant k and telemetry metrics
        self.k = self.sol_reserve * self.token_reserve
        self.last_update_time = now
        self.last_slot = max(self.last_slot, slot)
        self.cumulative_swaps += 1
        self.cumulative_sol_volume += sol_amount
        self.last_swap_is_buy = is_buy
        self.last_swap_sol = sol_amount

        return self.marginal_price_sol

    def apply_account_reserves(
        self,
        sol_reserve: float,
        token_reserve: float,
        slot: int = 0,
        timestamp: Optional[datetime] = None,
    ) -> float:
        """
        Synchronizes ground-truth reserves directly from on-chain accountChange notifications.
        Re-anchors the constant product k invariant.
        """
        if sol_reserve <= 0 or token_reserve <= 0:
            logger.warning(
                "Invalid reserves in accountChange for pool %s: sol=%.4f, token=%.4f",
                self.pool_address, sol_reserve, token_reserve
            )
            return self.marginal_price_sol

        self.sol_reserve = sol_reserve
        self.token_reserve = token_reserve
        self.k = sol_reserve * token_reserve
        self.last_update_time = timestamp or datetime.now(timezone.utc)
        self.last_slot = max(self.last_slot, slot)

        return self.marginal_price_sol


class VirtualAMMMirror:
    """
    Maintains a local mirror of every candidate pool on the live stream.
    Dictionary mapping: {"pool_address": VirtualPool}
    Recalculates exact reserves on every swapEvent and accountChange event.
    """

    def __init__(self):
        self._pools: Dict[str, VirtualPool] = {}

    @property
    def pools_dict(self) -> Dict[str, Dict[str, float]]:
        """
        Maintains a local dictionary of every candidate pool:
        {"pool_address": {"sol_reserve": X, "token_reserve": Y, "k": X*Y}}
        """
        return {
            addr: {
                "sol_reserve": pool.sol_reserve,
                "token_reserve": pool.token_reserve,
                "k": pool.k,
            }
            for addr, pool in self._pools.items()
        }

    def __getitem__(self, pool_address: str) -> Dict[str, float]:
        """Index access returning {"sol_reserve": X, "token_reserve": Y, "k": X*Y}."""
        pool = self._pools[pool_address]
        return {
            "sol_reserve": pool.sol_reserve,
            "token_reserve": pool.token_reserve,
            "k": pool.k,
        }

    def __contains__(self, pool_address: str) -> bool:
        return pool_address in self._pools

    def register_pool(
        self,
        pool_address: str,
        token_mint: str,
        initial_sol_reserve: float,
        initial_token_reserve: float = 1_000_000_000.0,
        venue_fee_pct: float = 1.25,
        slot: int = 0,
    ) -> VirtualPool:
        """Registers a newly discovered pool into the local AMM mirror."""
        pool = VirtualPool(
            pool_address=pool_address,
            token_mint=token_mint,
            sol_reserve=initial_sol_reserve,
            token_reserve=initial_token_reserve,
            venue_fee_pct=venue_fee_pct,
            last_slot=slot,
        )
        self._pools[pool_address] = pool
        logger.info(
            "Registered Virtual AMM Pool: %s (SOL: %.2f, Tokens: %.0f, Marginal Price: %.8f SOL, k: %.2e)",
            pool_address, initial_sol_reserve, initial_token_reserve, pool.marginal_price_sol, pool.k
        )
        return pool

    def get_pool(self, pool_address: str) -> Optional[VirtualPool]:
        """Retrieves a tracked VirtualPool by address."""
        return self._pools.get(pool_address)

    def has_pool(self, pool_address: str) -> bool:
        """Checks if pool is mirrored."""
        return pool_address in self._pools

    def get_all_pools(self) -> Dict[str, VirtualPool]:
        """Returns all currently mirrored pools."""
        return self._pools

    def process_swap_event(
        self,
        pool_address: str,
        is_buy: bool,
        sol_amount: float,
        token_amount: Optional[float] = None,
        slot: int = 0,
        timestamp: Optional[datetime] = None,
    ) -> Optional[VirtualPool]:
        """
        Recalculates local AMM reserves immediately upon observing a swap on the live stream.
        """
        pool = self._pools.get(pool_address)
        if not pool:
            return None

        pool.apply_swap(
            is_buy=is_buy,
            sol_amount=sol_amount,
            token_amount=token_amount,
            slot=slot,
            timestamp=timestamp,
        )
        return pool

    def process_account_change(
        self,
        pool_address: str,
        sol_reserve: float,
        token_reserve: float,
        slot: int = 0,
        timestamp: Optional[datetime] = None,
    ) -> Optional[VirtualPool]:
        """
        Applies ground-truth reserve balance updates from on-chain accountChange notifications.
        """
        pool = self._pools.get(pool_address)
        if not pool:
            return None

        pool.apply_account_reserves(
            sol_reserve=sol_reserve,
            token_reserve=token_reserve,
            slot=slot,
            timestamp=timestamp,
        )
        return pool

    def get_marginal_price(self, pool_address: str) -> Optional[float]:
        """Returns the instantaneous exact price in SOL for a pool."""
        pool = self._pools.get(pool_address)
        return pool.marginal_price_sol if pool else None

    def quote_exact_swap(
        self,
        pool_address: str,
        sol_in: float,
        is_buy: bool,
    ) -> Optional[SwapQuote]:
        """Computes exact execution quote with non-linear price impact."""
        pool = self._pools.get(pool_address)
        if not pool:
            return None
        return pool.quote_swap(sol_in=sol_in, is_buy=is_buy)
