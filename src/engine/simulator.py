"""
Deterministic Path A+ (100% Dynamic Trailing Single Exit) Paper Trading Simulator.
Enforces Guardrail 2 (2 swaps total, 0 tranche dilution) and Guardrail 7 (20m fallback timeout).
"""

from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Tuple
from src.engine.models import (
    PaperPosition,
    PositionState,
    ExecutionLeg,
)
from src.engine.paper_ledger import PaperLedger
from src.cost.fee_tiers import FeeTierLookup
from src.cost.jito_tips import JitoTipModel


class PathASimulator:
    """
    Simulates Path A+ execution for a candidate position over chronological price/reserve updates.
    """

    FALLBACK_HOLDING_TIMEOUT_MINUTES = 20.0  # Guardrail 7: 20-minute fallback timeout
    PROTECTIVE_STOP_LOSS_PCT = 0.10          # Initial hard protective stop at -10%

    def __init__(
        self,
        ledger: Optional[PaperLedger] = None,
        sol_price_usd: float = 155.0,
    ):
        self.ledger = ledger or PaperLedger()
        self.sol_price_usd = sol_price_usd
        self.fee_lookup = FeeTierLookup()
        self.tip_model = JitoTipModel(sol_price_usd)

    def open_position(
        self,
        position_id: str,
        pool_address: str,
        token_mint: str,
        regime: str,
        market_cap_sol: float,
        entry_price_sol: float,
        ratchet_trigger_gross_pct: float,
        position_notional_usd: float = 15.0,
        trailing_pct: float = 0.12,
        entry_time: Optional[datetime] = None,
    ) -> PaperPosition:
        """
        Executes the entry leg for a candidate. Strictly Leg 1 of 2.
        """
        now = entry_time or datetime.now(timezone.utc)
        sol_spent = position_notional_usd / self.sol_price_usd
        tokens_held = sol_spent / entry_price_sol if entry_price_sol > 0 else 0.0

        # Calculate entry fees
        tier = self.fee_lookup.get_tier(market_cap_sol)
        venue_fee_sol = sol_spent * (tier.total_fee_pct / 100.0)
        overhead = self.tip_model.calculate_entry_overhead(position_notional_usd)

        entry_leg = ExecutionLeg(
            leg_type="ENTRY",
            timestamp=now,
            price_sol=entry_price_sol,
            sol_amount=sol_spent,
            token_amount=tokens_held,
            venue_fee_sol=venue_fee_sol,
            jito_tip_sol=overhead.jito_tip_sol,
            priority_fee_sol=overhead.priority_fee_sol,
            base_fee_sol=overhead.base_tx_fee_sol,
        )

        # Calculate Ratchet Hurdle & Ratchet Floor Prices
        trigger_mult = 1.0 + (ratchet_trigger_gross_pct / 100.0)
        ratchet_trigger_price = entry_price_sol * trigger_mult

        # Ratchet floor locks in net profit (+8% to +10% net, covering fees + tips)
        net_lock_pct = 0.09 if regime == "REGIME_A" else 0.07
        ratchet_floor_price = entry_price_sol * (1.0 + net_lock_pct + (tier.total_fee_pct * 2.0 / 100.0))

        initial_stop_price = entry_price_sol * (1.0 - self.PROTECTIVE_STOP_LOSS_PCT)

        pos = PaperPosition(
            position_id=position_id,
            pool_address=pool_address,
            token_mint=token_mint,
            regime=regime,
            entry_time=now,
            entry_price_sol=entry_price_sol,
            tokens_held=tokens_held,
            sol_spent=sol_spent,
            position_notional_usd=position_notional_usd,
            ratchet_trigger_price_sol=ratchet_trigger_price,
            ratchet_floor_price_sol=ratchet_floor_price,
            trailing_pct=trailing_pct,
            peak_price_sol=entry_price_sol,
            current_stop_price_sol=initial_stop_price,
            state=PositionState.OPEN,
            ratchet_activated=False,
            entry_leg=entry_leg,
        )
        return pos

    def update_price_tick(
        self,
        pos: PaperPosition,
        current_price_sol: float,
        tick_time: datetime,
    ) -> Optional[PaperPosition]:
        """
        Updates position state with a new price tick.
        If an exit condition is met, liquidates 100% of tokens (Leg 2 of 2) and records to ledger.
        """
        if pos.state in (
            PositionState.CLOSED_TRAIL,
            PositionState.CLOSED_STOP,
            PositionState.CLOSED_TIMEOUT,
            PositionState.CLOSED_EMERGENCY,
        ):
            return pos

        # Update peak price
        if current_price_sol > pos.peak_price_sol:
            pos.peak_price_sol = current_price_sol

        # Check 1: Profit Hurdle Ratchet Activation
        if not pos.ratchet_activated and current_price_sol >= pos.ratchet_trigger_price_sol:
            pos.ratchet_activated = True
            pos.state = PositionState.RATCHETED
            # Ratchet floor locked in! Stop now trails peak price
            pos.current_stop_price_sol = max(pos.ratchet_floor_price_sol, pos.peak_price_sol * (1.0 - pos.trailing_pct))

        # Check 2: Dynamic Trailing Stop Update
        if pos.ratchet_activated:
            trailing_stop = pos.peak_price_sol * (1.0 - pos.trailing_pct)
            pos.current_stop_price_sol = max(pos.ratchet_floor_price_sol, trailing_stop)

        # Check 3: 20-Minute Fallback Timeout
        elapsed_min = (tick_time - pos.entry_time).total_seconds() / 60.0
        if elapsed_min >= self.FALLBACK_HOLDING_TIMEOUT_MINUTES:
            return self._execute_exit(pos, current_price_sol, tick_time, PositionState.CLOSED_TIMEOUT, "20M_TIMEOUT")

        # Check 4: Stop Trigger
        if current_price_sol <= pos.current_stop_price_sol:
            if pos.ratchet_activated:
                return self._execute_exit(pos, current_price_sol, tick_time, PositionState.CLOSED_TRAIL, "TRAILING_STOP")
            else:
                return self._execute_exit(pos, current_price_sol, tick_time, PositionState.CLOSED_STOP, "HARD_STOP")

        return pos

    def _execute_exit(
        self,
        pos: PaperPosition,
        exit_price_sol: float,
        exit_time: datetime,
        final_state: PositionState,
        reason: str,
    ) -> PaperPosition:
        """
        Executes the single full exit leg (Leg 2 of 2). 100% of position liquidated.
        """
        gross_sol_received = pos.tokens_held * exit_price_sol
        mc_approx = (exit_price_sol * 1_000_000_000.0)

        # Exit fees
        tier = self.fee_lookup.get_tier(mc_approx)
        venue_fee_sol = gross_sol_received * (tier.total_fee_pct / 100.0)
        overhead = self.tip_model.calculate_exit_overhead(pos.position_notional_usd, is_emergency=False)

        exit_leg = ExecutionLeg(
            leg_type="EXIT",
            timestamp=exit_time,
            price_sol=exit_price_sol,
            sol_amount=gross_sol_received,
            token_amount=pos.tokens_held,
            venue_fee_sol=venue_fee_sol,
            jito_tip_sol=overhead.jito_tip_sol,
            priority_fee_sol=overhead.priority_fee_sol,
            base_fee_sol=overhead.base_tx_fee_sol,
        )

        pos.exit_leg = exit_leg
        pos.exit_time = exit_time
        pos.state = final_state
        pos.exit_reason = reason

        # Persist trade to ledger
        self.ledger.record_trade(pos, self.sol_price_usd)
        return pos
