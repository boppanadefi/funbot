"""
Fix 4: Slot-Level (400ms) Order Flow Dynamics & Wash Trading Defense Engine.
Replaces scrapped Phase 2 historical wallet funding graphs with real-time AMM order flow dynamics.
Eliminates lagging 1-minute rolling windows (which are 150 blocks stale and trivially spoofable by syndicates):

1. Slot-Level (400ms) Delta Unique Buyers (last 5 blocks):
   - Solana block time is 400ms -> 5 blocks = 2.0s window.
   - Requires real-time progression of distinct new buyers across the last 5 blocks:
     delta_unique_buyers_5blocks >= 1.
   - Prevents syndicates from spoofing entry by firing 20 fresh wallets in block N, triggering the bot in block N+1,
     and dumping in block N+2. Real-time slot flow demands continued organic buyer inflow.

2. Breakout Candle Taker Buy Dominance (> 70%):
   - Requires that > 70% of the SOL moved in the breakout candle was Taker Buy.
   - Ensures curve momentum is overwhelmingly aggressive market-buy led (> 70%),
     defeating balanced wash trading and simultaneous syndicate bag distribution.
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
    unique_buyers_current_1m: int = 0
    unique_buyers_prior_1m: int = 0
    vuw: float = 0.0
    taker_buy_sol: float = 0.0
    taker_sell_sol: float = 0.0
    aggression_ratio: float = 0.0
    delta_unique_buyers_5blocks: int = 0
    unique_buyers_last_5_blocks: int = 0
    unique_buyers_prior_5_blocks: int = 0
    breakout_taker_buy_pct: float = 0.0
    is_approved: bool = False
    rejection_reasons: List[str] = field(default_factory=list)
    classification: str = "ORGANIC_RETAIL"  # "ORGANIC_RETAIL", "WASH_TRADING_SYBIL", "LOW_AGGRESSION"


class OrderFlowDynamicsEngine:
    """
    Evaluates real-time AMM order flow dynamics at slot-level (400ms) resolution
    to defeat syndicates and wash traders.
    """
    MIN_DELTA_UNIQUE_BUYERS_5BLOCKS: int = 1
    MIN_BREAKOUT_TAKER_BUY_PCT: float = 70.0
    MIN_VUW_RATIO: float = 1.20
    MIN_AGGRESSION_RATIO: float = 2.00

    def __init__(
        self,
        min_delta_unique_buyers_5blocks: int = 1,
        min_breakout_taker_buy_pct: float = 70.0,
        min_vuw_ratio: float = 1.20,
        min_aggression_ratio: float = 2.00,
    ):
        self.min_delta_unique_buyers_5blocks = min_delta_unique_buyers_5blocks
        self.min_breakout_taker_buy_pct = min_breakout_taker_buy_pct
        self.min_vuw_ratio = min_vuw_ratio
        self.min_aggression_ratio = min_aggression_ratio

    def compute_slot_delta_unique_buyers(
        self,
        buyers_last_5_blocks: int,
        buyers_prior_5_blocks: int = 0,
    ) -> int:
        """
        Computes Delta Unique Buyers over the last 5 blocks (400ms slots ~ 2.0s).
        Organic momentum requires new distinct buyers entering into the breakout (delta >= 1).
        Syndicates executing 1-block spoof batches in block N show 0 new buyers in subsequent blocks.
        """
        return int(buyers_last_5_blocks - buyers_prior_5_blocks)

    def compute_breakout_taker_buy_pct(
        self,
        taker_buy_sol: float,
        taker_sell_sol: float,
    ) -> float:
        """
        Computes the percentage of SOL moved in the breakout candle that was Taker Buy.
        Rule: Must be > 70.0% (> 0.70).
        """
        total_sol = taker_buy_sol + taker_sell_sol
        if total_sol <= 0.0:
            return 0.0
        return round((taker_buy_sol / total_sol) * 100.0, 2)

    def compute_vuw(self, current_1m_buyers: int, prior_1m_buyers: int) -> float:
        """
        Velocity of Unique Wallets (VUW) = (Unique Buyers Current 1m) / (Unique Buyers Prior 1m).
        """
        if current_1m_buyers <= 0:
            return 0.0
        if prior_1m_buyers <= 0:
            return float(current_1m_buyers)
        return round(float(current_1m_buyers) / float(prior_1m_buyers), 4)

    def compute_aggression_ratio(self, taker_buy_sol: float, taker_sell_sol: float) -> float:
        """
        Aggression Ratio = SOL spent into AMM curve (Taker Buy) / SOL pulled from curve (Taker Sell).
        """
        if taker_buy_sol <= 0.0:
            return 0.0
        if taker_sell_sol <= 0.0:
            return 999.0  # Pure market buying, zero sells
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
        delta_unique_buyers_5blocks: Optional[int] = None,
        breakout_taker_buy_pct: Optional[float] = None,
        now: Optional[datetime] = None,
    ) -> OrderFlowMetrics:
        """
        Evaluates real-time slot-level order flow dynamics:
        1. Delta Unique Buyers over the last 5 blocks (400ms slots) >= 1.
        2. Breakout Candle Taker Buy Dominance > 70% of SOL volume.
        """
        ts = now or datetime.now(timezone.utc)
        rejection_reasons: List[str] = []

        cur_buyers = unique_buyers_current_1m
        pri_buyers = unique_buyers_prior_1m
        buy_sol = taker_buy_sol
        sell_sol = taker_sell_sol
        delta_5blocks = delta_unique_buyers_5blocks
        b_buy_pct = breakout_taker_buy_pct
        u_last_5 = 0
        u_prior_5 = 0

        # 1. Parse swaps if provided
        if swaps is not None and len(swaps) > 0:
            current_swaps = []
            prior_swaps = []
            last_5_block_swaps = []
            prior_5_block_swaps = []

            has_slots = any("slot" in s for s in swaps)
            has_timestamps = any("timestamp" in s for s in swaps)

            if has_slots:
                max_slot = max(s.get("slot", 0) for s in swaps)
                for s in swaps:
                    slot = s.get("slot", 0)
                    slot_diff = max_slot - slot
                    # Slot-level 5-block windows (400ms per slot)
                    if slot_diff < 5:
                        last_5_block_swaps.append(s)
                    elif 5 <= slot_diff < 10:
                        prior_5_block_swaps.append(s)
                    # Macro 1-minute windows (150 slots)
                    if slot_diff <= 150:
                        current_swaps.append(s)
                    elif 150 < slot_diff <= 300:
                        prior_swaps.append(s)
            elif has_timestamps:
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

                sub_2s = [
                    s for s in current_swaps
                    if isinstance(s.get("timestamp"), datetime) and (ts - s["timestamp"]).total_seconds() <= 2.0
                ]
                if len(sub_2s) > 0:
                    last_5_block_swaps = sub_2s
                    prior_5_block_swaps = [
                        s for s in current_swaps
                        if isinstance(s.get("timestamp"), datetime) and 2.0 < (ts - s["timestamp"]).total_seconds() <= 4.0
                    ]
                else:
                    last_5_block_swaps = current_swaps[-min(5, len(current_swaps)):]
                    prior_5_block_swaps = current_swaps[-min(10, len(current_swaps)):-min(5, len(current_swaps))]
            else:
                mid = len(swaps) // 2
                prior_swaps = swaps[:mid]
                current_swaps = swaps[mid:]
                last_5_block_swaps = current_swaps[-min(5, len(current_swaps)):]
                prior_5_block_swaps = current_swaps[-min(10, len(current_swaps)):-min(5, len(current_swaps))]

            # Compute slot-level unique buyers
            u_last_5 = len({s.get("trader_pubkey") for s in last_5_block_swaps if s.get("is_buy") and s.get("trader_pubkey")})
            u_prior_5 = len({s.get("trader_pubkey") for s in prior_5_block_swaps if s.get("is_buy") and s.get("trader_pubkey")})
            if delta_5blocks is None:
                delta_5blocks = u_last_5 - u_prior_5

            # Compute macro buyers
            if cur_buyers is None:
                cur_buyers = len({s.get("trader_pubkey") for s in current_swaps if s.get("is_buy") and s.get("trader_pubkey")})
            if pri_buyers is None:
                pri_buyers = len({s.get("trader_pubkey") for s in prior_swaps if s.get("is_buy") and s.get("trader_pubkey")})

            # Compute SOL amounts in breakout window
            if buy_sol is None:
                active_swaps = current_swaps if len(current_swaps) > 0 else last_5_block_swaps
                buy_sol = sum(float(s.get("sol_amount", 0.0)) for s in active_swaps if s.get("is_buy"))
            if sell_sol is None:
                active_swaps = current_swaps if len(current_swaps) > 0 else last_5_block_swaps
                sell_sol = sum(float(s.get("sol_amount", 0.0)) for s in active_swaps if not s.get("is_buy"))

        # 2. Parse from quote if provided
        if quote is not None:
            if delta_5blocks is None:
                if "delta_unique_buyers_5blocks" in quote:
                    delta_5blocks = int(quote["delta_unique_buyers_5blocks"])
                elif "delta_unique_buyers" in quote:
                    delta_5blocks = int(quote["delta_unique_buyers"])
                elif "unique_buyers_last_5_blocks" in quote:
                    u_last_5 = int(quote["unique_buyers_last_5_blocks"])
                    u_prior_5 = int(quote.get("unique_buyers_prior_5_blocks", 0))
                    delta_5blocks = u_last_5 - u_prior_5

            if b_buy_pct is None and "breakout_taker_buy_pct" in quote:
                b_buy_pct = float(quote["breakout_taker_buy_pct"])

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

        # Normalize values
        cur_buyers = cur_buyers if cur_buyers is not None else 0
        pri_buyers = pri_buyers if pri_buyers is not None else 0
        buy_sol = buy_sol if buy_sol is not None else 0.0
        sell_sol = sell_sol if sell_sol is not None else 0.0

        # If delta_5blocks was not directly provided, derive from buyer counts
        if delta_5blocks is None:
            if u_last_5 > 0:
                delta_5blocks = u_last_5 - u_prior_5
            elif cur_buyers is not None and pri_buyers is not None:
                # If macro buyers are flat (e.g. 5 vs 5), slot delta is 0
                delta_5blocks = max(0, cur_buyers - pri_buyers)
            else:
                delta_5blocks = 0

        # Compute breakout candle taker buy dominance %
        total_sol = buy_sol + sell_sol
        if b_buy_pct is None:
            b_buy_pct = self.compute_breakout_taker_buy_pct(buy_sol, sell_sol)

        vuw = self.compute_vuw(cur_buyers, pri_buyers)
        aggression = self.compute_aggression_ratio(buy_sol, sell_sol)

        classification = "ORGANIC_RETAIL"

        # Rule 1: Slot-Level Delta Unique Buyers (last 5 blocks) >= 1
        # Replaces 1m VUW with 5-block delta to defeat syndicates spoofing 1m windows in block N
        if delta_5blocks is not None and (delta_unique_buyers_5blocks is not None or cur_buyers == 0):
            if delta_5blocks < self.min_delta_unique_buyers_5blocks:
                rejection_reasons.append(
                    f"VUW Rejected (Slot-Level Delta Unique Buyers Gate): "
                    f"Delta unique buyers over last 5 blocks ({delta_5blocks}) < {self.min_delta_unique_buyers_5blocks} floor "
                    f"(no real-time buyer acceleration; flat wash trading rotation or syndicate spoof)"
                )
                classification = "WASH_TRADING_SYBIL"
        elif delta_5blocks < self.min_delta_unique_buyers_5blocks or vuw < self.min_vuw_ratio:
            rejection_reasons.append(
                f"VUW Rejected (Slot-Level Delta Unique Buyers Gate): "
                f"Delta unique buyers over last 5 blocks ({delta_5blocks}) < {self.min_delta_unique_buyers_5blocks} floor "
                f"or VUW {vuw:.2f} < {self.min_vuw_ratio:.2f} floor "
                f"({cur_buyers} current / {pri_buyers} prior; flat wash trading rotation or syndicate spoof)"
            )
            classification = "WASH_TRADING_SYBIL"

        # Rule 2: Breakout Candle Taker Buy Dominance > 70.0%
        # Replaces 1m Aggression Ratio with >70% buy dominance of SOL moved in breakout candle
        if b_buy_pct is not None and (breakout_taker_buy_pct is not None or total_sol > 0):
            if b_buy_pct <= self.min_breakout_taker_buy_pct or aggression <= self.min_aggression_ratio:
                rejection_reasons.append(
                    f"Aggression Ratio Rejected (Breakout Candle Taker Buy Gate): "
                    f"Breakout candle Taker Buy dominance {b_buy_pct:.1f}% <= {self.min_breakout_taker_buy_pct:.1f}% floor "
                    f"({buy_sol:.2f} SOL buys / {total_sol:.2f} SOL total; Aggression ratio {aggression:.2f} <= {self.min_aggression_ratio:.2f}; "
                    f"weak buy aggression or simultaneous syndicate dump)"
                )
                if classification == "ORGANIC_RETAIL":
                    classification = "LOW_AGGRESSION"
        elif aggression <= self.min_aggression_ratio:
            rejection_reasons.append(
                f"Aggression Ratio Rejected (Breakout Candle Taker Buy Gate): "
                f"Aggression ratio {aggression:.2f} <= {self.min_aggression_ratio:.2f} floor "
                f"({buy_sol:.2f} SOL buys / {sell_sol:.2f} SOL sells; weak buy aggression or balanced wash trading)"
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
            delta_unique_buyers_5blocks=delta_5blocks,
            unique_buyers_last_5_blocks=u_last_5,
            unique_buyers_prior_5_blocks=u_prior_5,
            breakout_taker_buy_pct=b_buy_pct,
            is_approved=is_approved,
            rejection_reasons=rejection_reasons,
            classification=classification,
        )
