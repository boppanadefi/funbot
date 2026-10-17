"""
Fix 4: Real-Time Order Flow Dynamics & Wash Trading Defense Engine.
Replaces scrapped Phase 2 historical wallet funding graphs with real-time AMM order flow dynamics:

1. Velocity of Unique Wallets (VUW):
   - Rule: (Unique Buyers Current 1m) / (Unique Buyers Prior 1m)
   - Logic: Wash traders rotate the same 5-10 wallets, resulting in flat VUW (~1.0).
     Organic retail FOMO exhibits accelerating VUW (> 1.5).
   - Hard reject if VUW < 1.2 on a breakout.

2. Aggression Ratio:
   - Rule: SOL spent directly into the AMM curve (Taker Buy) / SOL pulled from the curve (Taker Sell)
   - Logic: Organic breakouts are heavily aggressive-buy led (market buying into the curve).
     Wash trading is often balanced (~1.0) or uses limit-like pacing.
   - Require Aggression Ratio > 2.0 to validate a breakout.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
import logging
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger("OrderFlowDynamics")


@dataclass
class OrderFlowMetrics:
    pool_address: str
    token_mint: str
    evaluated_ts: datetime
    unique_buyers_current_1m: int
    unique_buyers_prior_1m: int
    vuw: float
    taker_buy_sol: float
    taker_sell_sol: float
    aggression_ratio: float
    is_approved: bool
    rejection_reasons: List[str] = field(default_factory=list)
    classification: str = "ORGANIC_RETAIL"  # "ORGANIC_RETAIL", "WASH_TRADING_SYBIL", "LOW_AGGRESSION"


class OrderFlowDynamicsEngine:
    """
    Evaluates real-time AMM order flow dynamics to defeat syndicates and wash traders.
    """
    MIN_VUW_RATIO: float = 1.20
    MIN_AGGRESSION_RATIO: float = 2.00

    def __init__(
        self,
        min_vuw_ratio: float = 1.20,
        min_aggression_ratio: float = 2.00,
    ):
        self.min_vuw_ratio = min_vuw_ratio
        self.min_aggression_ratio = min_aggression_ratio

    def compute_vuw(self, current_1m_buyers: int, prior_1m_buyers: int) -> float:
        """
        Velocity of Unique Wallets (VUW) = (Unique Buyers Current 1m) / (Unique Buyers Prior 1m).
        - Wash traders rotate the same 5-10 wallets -> flat VUW ~ 1.0.
        - Organic retail FOMO exhibits accelerating VUW > 1.5.
        - Hard reject if VUW < 1.2 on a breakout.
        """
        if current_1m_buyers <= 0:
            return 0.0
        if prior_1m_buyers <= 0:
            # First surge from zero buyers: if current buyers >= 2, treat as accelerated
            return float(current_1m_buyers)
        return round(float(current_1m_buyers) / float(prior_1m_buyers), 4)

    def compute_aggression_ratio(self, taker_buy_sol: float, taker_sell_sol: float) -> float:
        """
        Aggression Ratio = SOL spent into AMM curve (Taker Buy) / SOL pulled from curve (Taker Sell).
        - Organic breakouts are heavily aggressive-buy led (> 2.0).
        - Wash trading is balanced (~1.0) or uses limit-like pacing.
        - Require Aggression Ratio > 2.0 to validate a breakout.
        """
        if taker_buy_sol <= 0.0:
            return 0.0
        if taker_sell_sol <= 0.0:
            return 999.0  # Infinite buy aggression (pure market buying, zero sells)
        return round(float(taker_buy_sol) / float(taker_sell_sol), 4)

    def evaluate_order_flow(
        self,
        pool_address: str,
        token_mint: str,
        swaps: Optional[List[Dict]] = None,
        quote: Optional[Dict] = None,
        unique_buyers_current_1m: Optional[int] = None,
        unique_buyers_prior_1m: Optional[int] = None,
        taker_buy_sol: Optional[float] = None,
        taker_sell_sol: Optional[float] = None,
        now: Optional[datetime] = None,
    ) -> OrderFlowMetrics:
        """
        Evaluates real-time order flow dynamics against VUW >= 1.2 and Aggression Ratio > 2.0.
        """
        ts = now or datetime.now(timezone.utc)
        rejection_reasons: List[str] = []

        cur_buyers = unique_buyers_current_1m
        pri_buyers = unique_buyers_prior_1m
        buy_sol = taker_buy_sol
        sell_sol = taker_sell_sol

        # 1. Parse swaps if provided
        if swaps is not None and len(swaps) > 0:
            current_swaps = []
            prior_swaps = []
            has_timestamps = any("timestamp" in s for s in swaps)

            if has_timestamps:
                for s in swaps:
                    s_ts = s.get("timestamp")
                    if isinstance(s_ts, str):
                        try:
                            s_ts = datetime.fromisoformat(s_ts.replace("Z", "+00:00"))
                        except Exception:
                            s_ts = ts
                    diff_s = (ts - s_ts).total_seconds()
                    if 0 <= diff_s <= 60.0:
                        current_swaps.append(s)
                    elif 60.0 < diff_s <= 120.0:
                        prior_swaps.append(s)
            elif any("slot" in s for s in swaps):
                max_slot = max(s.get("slot", 0) for s in swaps)
                for s in swaps:
                    slot_diff = max_slot - s.get("slot", 0)
                    if slot_diff <= 150:  # ~60 seconds on Solana (400ms slots)
                        current_swaps.append(s)
                    elif 150 < slot_diff <= 300:
                        prior_swaps.append(s)
            else:
                mid = len(swaps) // 2
                prior_swaps = swaps[:mid]
                current_swaps = swaps[mid:]

            if cur_buyers is None:
                cur_buyers = len({s.get("trader_pubkey") for s in current_swaps if s.get("is_buy") and s.get("trader_pubkey")})
            if pri_buyers is None:
                pri_buyers = len({s.get("trader_pubkey") for s in prior_swaps if s.get("is_buy") and s.get("trader_pubkey")})
            if buy_sol is None:
                buy_sol = sum(float(s.get("sol_amount", 0.0)) for s in current_swaps if s.get("is_buy"))
            if sell_sol is None:
                sell_sol = sum(float(s.get("sol_amount", 0.0)) for s in current_swaps if not s.get("is_buy"))

        # 2. Parse from quote if provided
        if quote is not None:
            if cur_buyers is None and "unique_buyers_current_1m" in quote:
                cur_buyers = int(quote["unique_buyers_current_1m"])
            if pri_buyers is None and "unique_buyers_prior_1m" in quote:
                pri_buyers = int(quote["unique_buyers_prior_1m"])
            if buy_sol is None and "taker_buy_sol" in quote:
                buy_sol = float(quote["taker_buy_sol"])
            if sell_sol is None and "taker_sell_sol" in quote:
                sell_sol = float(quote["taker_sell_sol"])

            # Support direct ratios if provided in mock/live quote
            if cur_buyers is None and "vuw" in quote:
                vuw_val = float(quote["vuw"])
                cur_buyers = int(vuw_val * 10)
                pri_buyers = 10
            if buy_sol is None and "aggression_ratio" in quote:
                agg_val = float(quote["aggression_ratio"])
                buy_sol = agg_val * 10.0
                sell_sol = 10.0

            # Derive from buy_volume_1m_usd if available
            if buy_sol is None and "buy_volume_1m_usd" in quote:
                b_usd = float(quote["buy_volume_1m_usd"])
                tot_usd = float(quote.get("volume_1m_usd", b_usd))
                s_usd = max(0.0, tot_usd - b_usd)
                sol_p = float(quote.get("sol_price_usd", 155.0))
                buy_sol = b_usd / sol_p
                sell_sol = s_usd / sol_p

        # Normalize None values
        cur_buyers = cur_buyers if cur_buyers is not None else 0
        pri_buyers = pri_buyers if pri_buyers is not None else 0
        buy_sol = buy_sol if buy_sol is not None else 0.0
        sell_sol = sell_sol if sell_sol is not None else 0.0

        vuw = self.compute_vuw(cur_buyers, pri_buyers)
        aggression = self.compute_aggression_ratio(buy_sol, sell_sol)

        classification = "ORGANIC_RETAIL"

        # Rule 1: VUW >= 1.2
        if vuw < self.min_vuw_ratio:
            rejection_reasons.append(
                f"VUW Rejected: Velocity of Unique Wallets {vuw:.2f} < {self.min_vuw_ratio:.2f} floor "
                f"({cur_buyers} current / {pri_buyers} prior; flat wash trading or stagnant FOMO)"
            )
            classification = "WASH_TRADING_SYBIL"

        # Rule 2: Aggression Ratio > 2.0
        if aggression <= self.min_aggression_ratio:
            rejection_reasons.append(
                f"Aggression Ratio Rejected: Taker Buy/Sell ratio {aggression:.2f} <= {self.min_aggression_ratio:.2f} "
                f"({buy_sol:.2f} SOL buys / {sell_sol:.2f} SOL sells; wash trading or weak buy aggression)"
            )
            if classification == "ORGANIC_RETAIL":
                classification = "LOW_AGGRESSION"

        is_approved = (len(rejection_reasons) == 0)

        return OrderFlowMetrics(
            pool_address=pool_address,
            token_mint=token_mint,
            evaluated_ts=ts,
            unique_buyers_current_1m=cur_buyers,
            unique_buyers_prior_1m=pri_buyers,
            vuw=vuw,
            taker_buy_sol=buy_sol,
            taker_sell_sol=sell_sol,
            aggression_ratio=aggression,
            is_approved=is_approved,
            rejection_reasons=rejection_reasons,
            classification=classification,
        )
