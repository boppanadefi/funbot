"""
Local-Structure & Time-Decay Exit Engine (Fix 3).
Replaces continuous percentage trailing stops with discrete, structural Solana AMM exits:

1. Structure-Based Trailing Stop:
   - Stop Loss = Lowest Low of the previous two completed 1-minute candles.
   - Absorbs 30-40% single-block wicks without clipping while macro 1m trend holds higher lows.

2. Momentum Time-Decay Exit:
   - If 2 consecutive 1-minute candles pass without making a new local high,
     AND volume drops > 40% from the entry-breakout volume, exit 100% immediately.
   - Do not wait for the trailing stop to be tagged.

3. Liquidity Collapse Exit:
   - Every 30 seconds, simulate a sell quote.
   - If the estimated slippage on your remaining position exceeds 5.0%, market dump immediately.
   - LP is draining; cannot afford to wait for a price stop.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
import logging
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger("StructureExits")


@dataclass
class Candle1M:
    minute_timestamp: datetime
    open_sol: float
    high_sol: float
    low_sol: float
    close_sol: float
    volume_usd: float = 0.0
    tick_count: int = 1
    is_closed: bool = False


@dataclass
class PositionExitEvaluation:
    should_exit: bool = False
    exit_type: Optional[str] = None  # "STRUCTURE_TRAILING_STOP", "MOMENTUM_TIME_DECAY", "LIQUIDITY_COLLAPSE"
    exit_reason: Optional[str] = None
    target_stop_price_sol: Optional[float] = None
    lowest_low_sol: Optional[float] = None


@dataclass
class PositionStructureState:
    position_id: str
    entry_price_sol: float
    entry_time: datetime
    entry_breakout_vol_usd: float = 0.0
    local_high_price_sol: float = 0.0
    consecutive_stagnant_candles: int = 0
    completed_candles: List[Candle1M] = field(default_factory=list)
    current_candle: Optional[Candle1M] = None
    last_liquidity_check_time: Optional[datetime] = None
    last_calculated_stop_sol: float = 0.0


class LocalStructureExitEngine:
    """
    Stateful engine enforcing Local-Structure & Time-Decay exits.
    """

    def __init__(
        self,
        lookback_candles: int = 2,
        momentum_stagnant_candles: int = 2,
        momentum_vol_drop_pct: float = 0.40,
        liquidity_check_interval_seconds: float = 30.0,
        liquidity_max_slippage_pct: float = 5.0,
    ):
        self.lookback_candles = lookback_candles
        self.momentum_stagnant_candles = momentum_stagnant_candles
        self.momentum_vol_drop_pct = momentum_vol_drop_pct
        self.liquidity_check_interval_seconds = liquidity_check_interval_seconds
        self.liquidity_max_slippage_pct = liquidity_max_slippage_pct
        self._states: Dict[str, PositionStructureState] = {}

    def register_position(
        self,
        position_id: str,
        entry_price_sol: float,
        entry_time: datetime,
        entry_breakout_vol_usd: float = 0.0,
    ) -> PositionStructureState:
        """Initializes structure tracking for a newly opened position."""
        state = PositionStructureState(
            position_id=position_id,
            entry_price_sol=entry_price_sol,
            entry_time=entry_time,
            entry_breakout_vol_usd=entry_breakout_vol_usd,
            local_high_price_sol=entry_price_sol,
            consecutive_stagnant_candles=0,
            completed_candles=[],
            current_candle=None,
            last_liquidity_check_time=entry_time,
            last_calculated_stop_sol=0.0,
        )
        self._states[position_id] = state
        return state

    def get_state(self, position_id: str) -> Optional[PositionStructureState]:
        return self._states.get(position_id)

    def record_completed_candle(
        self,
        position_id: str,
        candle: Candle1M,
    ) -> None:
        """Explicitly ingests a completed 1-minute candle."""
        state = self._states.get(position_id)
        if not state:
            return

        candle.is_closed = True
        # Clear intra-minute current_candle if it matches this minute
        if state.current_candle and state.current_candle.minute_timestamp <= candle.minute_timestamp:
            state.current_candle = None

        state.completed_candles.append(candle)

        # Check local high progression
        if candle.high_sol > state.local_high_price_sol:
            state.local_high_price_sol = candle.high_sol
            state.consecutive_stagnant_candles = 0
            logger.debug(
                "Position %s made new local high: %.8f SOL. Stagnant counter reset.",
                position_id, candle.high_sol
            )
        else:
            state.consecutive_stagnant_candles += 1
            logger.debug(
                "Position %s stagnant for %d consecutive candles (High %.8f <= Local High %.8f).",
                position_id, state.consecutive_stagnant_candles, candle.high_sol, state.local_high_price_sol
            )

        # Update structure trailing stop: Lowest Low of previous 2 completed candles
        if len(state.completed_candles) >= self.lookback_candles:
            recent_candles = state.completed_candles[-self.lookback_candles:]
            lowest_low = min(c.low_sol for c in recent_candles)
            # Stop only ratchets upward
            state.last_calculated_stop_sol = max(state.last_calculated_stop_sol, lowest_low)

    def ingest_tick(
        self,
        position_id: str,
        current_price_sol: float,
        tick_time: datetime,
        vol_1m_usd: float = 0.0,
    ) -> None:
        """
        Builds 1-minute candles dynamically from individual price & volume ticks.
        """
        state = self._states.get(position_id)
        if not state:
            state = self.register_position(position_id, current_price_sol, tick_time)

        minute_bin = tick_time.replace(second=0, microsecond=0)

        if state.current_candle is None:
            state.current_candle = Candle1M(
                minute_timestamp=minute_bin,
                open_sol=current_price_sol,
                high_sol=current_price_sol,
                low_sol=current_price_sol,
                close_sol=current_price_sol,
                volume_usd=vol_1m_usd,
                tick_count=1,
            )
        elif minute_bin > state.current_candle.minute_timestamp:
            # Current minute elapsed: close candle and start a new one
            self.record_completed_candle(position_id, state.current_candle)
            state.current_candle = Candle1M(
                minute_timestamp=minute_bin,
                open_sol=current_price_sol,
                high_sol=current_price_sol,
                low_sol=current_price_sol,
                close_sol=current_price_sol,
                volume_usd=vol_1m_usd,
                tick_count=1,
            )
        else:
            # Update active intra-minute candle
            c = state.current_candle
            if current_price_sol > c.high_sol:
                c.high_sol = current_price_sol
            if current_price_sol < c.low_sol:
                c.low_sol = current_price_sol
            c.close_sol = current_price_sol
            c.volume_usd = max(c.volume_usd, vol_1m_usd)
            c.tick_count += 1

    def evaluate_exit(
        self,
        position_id: str,
        current_price_sol: float,
        tick_time: datetime,
        remaining_tokens: float,
        current_token_reserve: Optional[float] = None,
        quote: Optional[Dict] = None,
        vol_1m_usd: Optional[float] = None,
    ) -> PositionExitEvaluation:
        """
        Evaluates the 3 structural exit conditions:
        1. Liquidity Collapse Exit (every 30s: slippage > 5%)
        2. Structure-Based Trailing Stop (Lowest Low of last 2 completed 1m candles)
        3. Momentum Time-Decay Exit (2 candles no high + >40% volume drop)
        """
        state = self._states.get(position_id)
        if not state:
            return PositionExitEvaluation(should_exit=False)

        # -------------------------------------------------------------
        # 1. Rule 3: Liquidity Collapse Exit (Checked Every 30 Seconds)
        # -------------------------------------------------------------
        check_liquidity = False
        if state.last_liquidity_check_time is None:
            check_liquidity = True
        else:
            elapsed_sec = (tick_time - state.last_liquidity_check_time).total_seconds()
            if elapsed_sec >= self.liquidity_check_interval_seconds:
                check_liquidity = True

        if check_liquidity:
            state.last_liquidity_check_time = tick_time

            # Compute estimated sell slippage
            est_slippage_pct = 0.0
            if quote and "sell_slippage_pct" in quote:
                est_slippage_pct = float(quote["sell_slippage_pct"])
            elif quote and "estimated_slippage_pct" in quote:
                est_slippage_pct = float(quote["estimated_slippage_pct"])
            elif quote and "price_impact_pct" in quote:
                est_slippage_pct = float(quote["price_impact_pct"])
            elif quote and float(quote.get("liquidity_usd", 9999.0) or 0.0) < 500.0:
                # Liquidity drained or rug-pulled on live AMM curve
                est_slippage_pct = 99.9
            elif quote and float(quote.get("quote_sol_reserve", 0.0) or 0.0) > 0:
                q_sol = float(quote["quote_sol_reserve"])
                remaining_sol = (remaining_tokens * current_price_sol) if current_price_sol > 0 else 0.10
                est_slippage_pct = (remaining_sol / (2.0 * q_sol)) * 100.0
            elif current_token_reserve and current_token_reserve > 0 and remaining_tokens > 0:
                est_slippage_pct = (remaining_tokens / (current_token_reserve + remaining_tokens)) * 100.0

            if est_slippage_pct > self.liquidity_max_slippage_pct:
                logger.warning(
                    "Position %s LIQUIDITY COLLAPSE: Estimated slippage %.2f%% > %.1f%% threshold! Draining LP.",
                    position_id, est_slippage_pct, self.liquidity_max_slippage_pct
                )
                return PositionExitEvaluation(
                    should_exit=True,
                    exit_type="LIQUIDITY_COLLAPSE",
                    exit_reason=(
                        f"Liquidity Collapse Exit: Simulated sell slippage {est_slippage_pct:.2f}% "
                        f"exceeds {self.liquidity_max_slippage_pct:.1f}% threshold (LP draining)"
                    ),
                )

        # -------------------------------------------------------------
        # 2. Rule 1: Structure-Based Trailing Stop Check
        # -------------------------------------------------------------
        # Stop Loss = Lowest Low of the previous two completed 1-minute candles.
        target_stop = None
        lowest_low = None
        if len(state.completed_candles) >= self.lookback_candles:
            recent_candles = state.completed_candles[-self.lookback_candles:]
            lowest_low = min(c.low_sol for c in recent_candles)
            target_stop = max(state.last_calculated_stop_sol, lowest_low)
            state.last_calculated_stop_sol = target_stop

            if current_price_sol <= target_stop:
                logger.info(
                    "Position %s STRUCTURE TRAILING STOP: Price %.8f SOL <= Lowest Low %.8f SOL of previous 2 candles.",
                    position_id, current_price_sol, target_stop
                )
                return PositionExitEvaluation(
                    should_exit=True,
                    exit_type="STRUCTURE_TRAILING_STOP",
                    exit_reason=(
                        f"Structure Trailing Stop: Price {current_price_sol:.8f} SOL tagged lowest low "
                        f"{target_stop:.8f} SOL of previous 2 completed 1m candles"
                    ),
                    target_stop_price_sol=target_stop,
                    lowest_low_sol=lowest_low,
                )

        # -------------------------------------------------------------
        # 3. Rule 2: Momentum Time-Decay Exit
        # -------------------------------------------------------------
        # If 2 consecutive 1m candles pass without making a new local high,
        # and volume drops > 40% from entry-breakout volume, exit 100% immediately
        # without waiting for trailing stop to be tagged.
        if state.consecutive_stagnant_candles >= self.momentum_stagnant_candles:
            current_vol = 0.0
            if vol_1m_usd is not None and vol_1m_usd > 0:
                current_vol = vol_1m_usd
            elif quote and "volume_1m_usd" in quote:
                current_vol = float(quote["volume_1m_usd"])
            elif state.completed_candles:
                current_vol = state.completed_candles[-1].volume_usd
            elif state.current_candle:
                current_vol = state.current_candle.volume_usd

            breakout_vol = state.entry_breakout_vol_usd
            if breakout_vol <= 0 and quote and "breakout_volume_usd" in quote:
                breakout_vol = float(quote["breakout_volume_usd"])
            elif breakout_vol <= 0 and state.completed_candles:
                breakout_vol = state.completed_candles[0].volume_usd

            if breakout_vol > 0:
                vol_drop_pct = (breakout_vol - current_vol) / breakout_vol
                if vol_drop_pct > self.momentum_vol_drop_pct:
                    logger.info(
                        "Position %s MOMENTUM TIME-DECAY: %d stagnant candles & vol drop %.1f%% (>40%%).",
                        position_id, state.consecutive_stagnant_candles, vol_drop_pct * 100.0
                    )
                    return PositionExitEvaluation(
                        should_exit=True,
                        exit_type="MOMENTUM_TIME_DECAY",
                        exit_reason=(
                            f"Momentum Time-Decay Exit: {state.consecutive_stagnant_candles} consecutive 1m candles "
                            f"without new high and volume dropped {vol_drop_pct*100.0:.1f}% (>40%) from entry "
                            f"(${current_vol:,.0f} vs ${breakout_vol:,.0f})"
                        ),
                        target_stop_price_sol=target_stop,
                        lowest_low_sol=lowest_low,
                    )

        return PositionExitEvaluation(
            should_exit=False,
            target_stop_price_sol=target_stop,
            lowest_low_sol=lowest_low,
        )
