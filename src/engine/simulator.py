"""
Deterministic Friction-Busting Scale-Out Paper Trading Simulator.
Enforces Invariant 2 (Path A+ prohibited for positions < $50; Friction-Busting Scale-Out mandatory:
TP1 sells 80% at +8% to +10% gross to cover round-trip friction; 20% runner trails with zero cost drag)
and Guardrail 7 (20m fallback timeout).
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
from src.engine.structure_exits import (
    LocalStructureExitEngine,
    Candle1M,
)


class PathASimulator:
    """
    Simulates Friction-Busting Scale-Out execution for positions < $50.
    1. Entry leg: 100% position acquired.
    2. TP1 ("The Friction Buster"): Sells 80% at modest +8% to +10% gross, completely covering trade friction.
    3. The Runner: Retains remaining 20% with zero cost-of-capital drag, trailing with a wide 15% stop.
    If hard stop (-10%) or 20m timeout is hit before TP1, liquidates 100% in a single exit.
    """

    FALLBACK_HOLDING_TIMEOUT_MINUTES = 20.0  # Guardrail 7: 20-minute fallback timeout
    PROTECTIVE_STOP_LOSS_PCT = 0.10          # Initial hard protective stop at -10%
    DEFAULT_TP1_TARGET_GROSS_PCT = 0.09      # +9.0% gross target (+8% to +10% range)
    DEFAULT_TP1_SIZE_PCT = 0.80              # 80% liquidated at TP1 to pay round-trip friction
    DEFAULT_RUNNER_SIZE_PCT = 0.20           # 20% retained as free runner
    DEFAULT_RUNNER_TRAILING_PCT = 0.15       # 15% wide trailing stop on runner

    def __init__(
        self,
        ledger: Optional[PaperLedger] = None,
        sol_price_usd: float = 155.0,
    ):
        self.ledger = ledger or PaperLedger()
        self.sol_price_usd = sol_price_usd
        self.fee_lookup = FeeTierLookup()
        self.tip_model = JitoTipModel(sol_price_usd)
        self.structure_exit_engine = LocalStructureExitEngine()

    def open_position(
        self,
        position_id: str,
        pool_address: str,
        token_mint: str,
        regime: str,
        market_cap_sol: float,
        entry_price_sol: float,
        ratchet_trigger_gross_pct: Optional[float] = None,
        tp1_target_gross_pct: Optional[float] = None,
        position_notional_usd: float = 15.0,
        trailing_pct: float = 0.15,
        entry_time: Optional[datetime] = None,
        entry_breakout_volume_usd: float = 0.0,
        trailing_mode: str = "STRUCTURE",
    ) -> PaperPosition:
        """
        Executes the entry leg for a candidate. Strictly Leg 1 of up to 3.
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

        # Determine TP1 target gross percentage (+8% to +10%, default +9%)
        if tp1_target_gross_pct is not None:
            resolved_tp1_target = tp1_target_gross_pct
        elif ratchet_trigger_gross_pct is not None:
            if 0.08 <= ratchet_trigger_gross_pct <= 0.10:
                resolved_tp1_target = ratchet_trigger_gross_pct
            elif 8.0 <= ratchet_trigger_gross_pct <= 10.0:
                resolved_tp1_target = ratchet_trigger_gross_pct / 100.0
            elif position_notional_usd < 50.0:
                # Path A+ prohibited for <$50; enforce Friction-Busting Scale-Out (+9% target)
                resolved_tp1_target = self.DEFAULT_TP1_TARGET_GROSS_PCT
            else:
                resolved_tp1_target = (
                    ratchet_trigger_gross_pct / 100.0
                    if ratchet_trigger_gross_pct >= 1.0
                    else ratchet_trigger_gross_pct
                )
        else:
            resolved_tp1_target = self.DEFAULT_TP1_TARGET_GROSS_PCT

        tp1_price_sol = entry_price_sol * (1.0 + resolved_tp1_target)
        initial_stop_price = entry_price_sol * (1.0 - self.PROTECTIVE_STOP_LOSS_PCT)
        runner_tokens = tokens_held * self.DEFAULT_RUNNER_SIZE_PCT

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
            tp1_target_gross_pct=resolved_tp1_target,
            tp1_price_sol=tp1_price_sol,
            tp1_size_pct=self.DEFAULT_TP1_SIZE_PCT,
            runner_size_pct=self.DEFAULT_RUNNER_SIZE_PCT,
            runner_tokens_held=runner_tokens,
            runner_trailing_pct=trailing_pct or self.DEFAULT_RUNNER_TRAILING_PCT,
            protective_stop_loss_pct=self.PROTECTIVE_STOP_LOSS_PCT,
            peak_price_sol=entry_price_sol,
            current_stop_price_sol=initial_stop_price,
            state=PositionState.OPEN,
            tp1_activated=False,
            entry_leg=entry_leg,
            entry_breakout_volume_usd=entry_breakout_volume_usd,
            trailing_mode=trailing_mode,
            # Backward compatibility attributes
            ratchet_trigger_price_sol=tp1_price_sol,
            ratchet_floor_price_sol=entry_price_sol * 1.01,
            trailing_pct=trailing_pct or self.DEFAULT_RUNNER_TRAILING_PCT,
            ratchet_activated=False,
        )
        self.structure_exit_engine.register_position(
            position_id=position_id,
            entry_price_sol=entry_price_sol,
            entry_time=now,
            entry_breakout_vol_usd=entry_breakout_volume_usd,
        )
        return pos

    def update_price_tick(
        self,
        pos: PaperPosition,
        current_price_sol: float,
        tick_time: datetime,
        vol_1m_usd: Optional[float] = None,
        quote: Optional[Dict] = None,
        current_token_reserve: Optional[float] = None,
        completed_candle: Optional[Candle1M] = None,
    ) -> Optional[PaperPosition]:
        """
        Updates position state with a new price tick.
        Enforces Friction-Busting Scale-Out and Local-Structure & Time-Decay Exits (Fix 3):
        - Evaluates Liquidity Collapse (30s interval: sell slippage > 5%).
        - Evaluates Momentum Time-Decay (2 candles no high + >40% volume drop).
        - Evaluates Structure-Based Trailing Stop (Lowest Low of previous two completed 1m candles).
        - Checks hard protective stop (-10%), TP1 trigger (+8% to +10%, default +9%), and 20m timeout.
        """
        if pos.is_closed:
            return pos

        # Update peak price
        if current_price_sol > pos.peak_price_sol:
            pos.peak_price_sol = current_price_sol

        # Check 1: 20-Minute Fallback Timeout
        elapsed_min = (tick_time - pos.entry_time).total_seconds() / 60.0
        if elapsed_min >= self.FALLBACK_HOLDING_TIMEOUT_MINUTES:
            return self._execute_exit(pos, current_price_sol, tick_time, PositionState.CLOSED_TIMEOUT, "20M_TIMEOUT")

        # Ingest candle or tick into LocalStructureExitEngine
        if completed_candle is not None:
            self.structure_exit_engine.record_completed_candle(pos.position_id, completed_candle)
        else:
            self.structure_exit_engine.ingest_tick(pos.position_id, current_price_sol, tick_time, vol_1m_usd or 0.0)

        remaining_tokens = pos.runner_tokens_held if pos.tp1_activated else pos.tokens_held

        # Evaluate Local-Structure & Time-Decay Exit Rules (Fix 3)
        struct_eval = self.structure_exit_engine.evaluate_exit(
            position_id=pos.position_id,
            current_price_sol=current_price_sol,
            tick_time=tick_time,
            remaining_tokens=remaining_tokens,
            current_token_reserve=current_token_reserve,
            quote=quote,
            vol_1m_usd=vol_1m_usd,
        )

        if struct_eval.target_stop_price_sol is not None:
            pos.structure_stop_price_sol = struct_eval.target_stop_price_sol
            pos.lowest_low_2m_candles_sol = struct_eval.lowest_low_sol or struct_eval.target_stop_price_sol
            if pos.trailing_mode == "STRUCTURE":
                pos.current_stop_price_sol = max(pos.current_stop_price_sol, struct_eval.target_stop_price_sol)

        # Trigger Structural Exits if signaled
        if struct_eval.should_exit:
            if struct_eval.exit_type == "LIQUIDITY_COLLAPSE":
                return self._execute_exit(pos, current_price_sol, tick_time, PositionState.CLOSED_LIQUIDITY_DRAIN, struct_eval.exit_reason or "LIQUIDITY_COLLAPSE")
            elif struct_eval.exit_type == "MOMENTUM_TIME_DECAY":
                return self._execute_exit(pos, current_price_sol, tick_time, PositionState.CLOSED_TIME_DECAY, struct_eval.exit_reason or "MOMENTUM_TIME_DECAY")
            elif struct_eval.exit_type == "STRUCTURE_TRAILING_STOP" and pos.trailing_mode == "STRUCTURE":
                return self._execute_exit(pos, current_price_sol, tick_time, PositionState.CLOSED_TRAIL, struct_eval.exit_reason or "STRUCTURE_TRAILING_STOP")

        # Stage 1: Position is OPEN (Awaiting TP1 or Stop)
        if not pos.tp1_activated:
            # Check Hard Protective Stop (-10%)
            if current_price_sol <= pos.current_stop_price_sol:
                return self._execute_exit(pos, current_price_sol, tick_time, PositionState.CLOSED_STOP, "HARD_STOP")

            # Check TP1 Trigger (+8% to +10% gross, e.g. +9%)
            if current_price_sol >= pos.tp1_price_sol:
                self._execute_tp1(pos, current_price_sol, tick_time)
                return pos

        # Stage 2: Runner Active (20% running with structure / fallback trailing stop)
        else:
            # Structure-based trailing stop: Lowest Low of previous 2 completed 1m candles.
            # If structure stop not yet formed (< 2 completed candles), fallback to trailing peak by runner_trailing_pct
            if pos.trailing_mode == "STRUCTURE" and pos.structure_stop_price_sol > 0:
                pos.current_stop_price_sol = pos.structure_stop_price_sol
            else:
                fallback_stop = pos.peak_price_sol * (1.0 - pos.runner_trailing_pct)
                pos.current_stop_price_sol = max(pos.current_stop_price_sol, fallback_stop)

            # Check Trailing Stop Trigger
            if current_price_sol <= pos.current_stop_price_sol:
                return self._execute_exit(pos, current_price_sol, tick_time, PositionState.CLOSED_TRAIL, "TRAILING_STOP")

        return pos

    def _execute_tp1(
        self,
        pos: PaperPosition,
        current_price_sol: float,
        tick_time: datetime,
    ) -> None:
        """
        Executes TP1 (The Friction Buster): Sells 80% of tokens at +8% to +10% gross.
        Covers the round-trip friction of the entire trade and unlocks a risk-free 20% runner.
        """
        tp1_tokens = pos.tokens_held * pos.tp1_size_pct
        gross_sol_received = tp1_tokens * current_price_sol
        mc_approx = current_price_sol * 1_000_000_000.0

        tier = self.fee_lookup.get_tier(mc_approx)
        venue_fee_sol = gross_sol_received * (tier.total_fee_pct / 100.0)
        tp1_notional_usd = pos.position_notional_usd * pos.tp1_size_pct
        overhead = self.tip_model.calculate_exit_overhead(tp1_notional_usd, is_emergency=False)

        tp1_leg = ExecutionLeg(
            leg_type="TP1_EXIT",
            timestamp=tick_time,
            price_sol=current_price_sol,
            sol_amount=gross_sol_received,
            token_amount=tp1_tokens,
            venue_fee_sol=venue_fee_sol,
            jito_tip_sol=overhead.jito_tip_sol,
            priority_fee_sol=overhead.priority_fee_sol,
            base_fee_sol=overhead.base_tx_fee_sol,
        )

        pos.tp1_leg = tp1_leg
        pos.tp1_activated = True
        pos.ratchet_activated = True  # Backward compatibility
        pos.state = PositionState.RUNNER_ACTIVE
        pos.tp1_realized_usd = (gross_sol_received - tp1_leg.total_friction_sol) * self.sol_price_usd

        # Initialize runner trailing stop: 15% below peak
        pos.peak_price_sol = max(pos.peak_price_sol, current_price_sol)
        pos.current_stop_price_sol = pos.peak_price_sol * (1.0 - pos.runner_trailing_pct)

    def _execute_exit(
        self,
        pos: PaperPosition,
        exit_price_sol: float,
        exit_time: datetime,
        final_state: PositionState,
        reason: str,
    ) -> PaperPosition:
        """
        Executes final exit.
        If TP1 was already executed, liquidates the remaining 20% runner tokens.
        If TP1 was not executed (hard stop or timeout), liquidates 100% of tokens in single exit.
        """
        if pos.tp1_activated:
            # Runner exit (20% of tokens)
            tokens_to_sell = pos.runner_tokens_held
            notional_usd = pos.position_notional_usd * pos.runner_size_pct
            leg_type = "RUNNER_EXIT"
        else:
            # Full exit (100% of tokens)
            tokens_to_sell = pos.tokens_held
            notional_usd = pos.position_notional_usd
            leg_type = "EXIT"

        gross_sol_received = tokens_to_sell * exit_price_sol
        mc_approx = exit_price_sol * 1_000_000_000.0

        tier = self.fee_lookup.get_tier(mc_approx)
        venue_fee_sol = gross_sol_received * (tier.total_fee_pct / 100.0)
        overhead = self.tip_model.calculate_exit_overhead(notional_usd, is_emergency=False)

        exit_leg = ExecutionLeg(
            leg_type=leg_type,
            timestamp=exit_time,
            price_sol=exit_price_sol,
            sol_amount=gross_sol_received,
            token_amount=tokens_to_sell,
            venue_fee_sol=venue_fee_sol,
            jito_tip_sol=overhead.jito_tip_sol,
            priority_fee_sol=overhead.priority_fee_sol,
            base_fee_sol=overhead.base_tx_fee_sol,
        )

        if pos.tp1_activated:
            pos.runner_leg = exit_leg
            pos.runner_realized_usd = (gross_sol_received - exit_leg.total_friction_sol) * self.sol_price_usd

        pos.exit_leg = exit_leg
        pos.exit_time = exit_time
        pos.state = final_state
        pos.exit_reason = reason

        # Persist trade to ledger
        self.ledger.record_trade(pos, self.sol_price_usd)
        return pos

