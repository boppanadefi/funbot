"""
Deterministic Position Lifecycle State Machine for Solana Momentum Trades.
Enforces Invariant 2 (Path A+ 100% Dynamic Trailing Single Exit) and strict transition graphs.
"""

from dataclasses import dataclass, field
from enum import Enum
import logging
import time
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger("PositionStateMachine")


class PositionState(str, Enum):
    IDLE = "IDLE"
    PENDING_ENTRY = "PENDING_ENTRY"
    ACTIVE_OPEN = "ACTIVE_OPEN"
    TRAILED_STOP_LOCKED = "TRAILED_STOP_LOCKED"
    TP1_SCALING = "TP1_SCALING"
    RUNNER_ACTIVE = "RUNNER_ACTIVE"
    PENDING_EXIT = "PENDING_EXIT"
    CLOSED = "CLOSED"
    CANCELLED = "CANCELLED"


class ExecutionMode(str, Enum):
    PATH_A_PLUS = "PATH_A_PLUS"      # 100% position trailing exit (strictly 2 swaps total)
    TRANCHE_RUNNER = "TRANCHE_RUNNER"  # 90% TP1 scale-out + 10% runner (for >= $50 size)


class IllegalStateTransitionError(Exception):
    """Raised when an invalid state transition is attempted."""
    pass


@dataclass(frozen=True)
class StateTransitionRecord:
    from_state: PositionState
    to_state: PositionState
    timestamp: float
    trigger: str
    price_sol: float
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class PositionContext:
    pool_id: str
    mint: str
    mode: ExecutionMode
    entry_price_sol: float
    size_tokens: int
    size_sol_notional: float
    entry_timestamp: float
    current_price_sol: float = 0.0
    peak_price_sol: float = 0.0
    stop_price_sol: float = 0.0
    profit_hurdle_sol: float = 0.0
    exit_price_sol: float = 0.0
    exit_timestamp: float = 0.0
    realized_pnl_sol: float = 0.0
    realized_pnl_pct: float = 0.0
    tx_signature_entry: Optional[str] = None
    tx_signature_exit: Optional[str] = None


class PositionStateMachine:
    """Manages lifecycle transitions for a single position with strict edge validation."""

    # Valid directed edges: from_state -> set of legal to_states
    _VALID_TRANSITIONS: Dict[PositionState, Set[PositionState]] = {
        PositionState.IDLE: {PositionState.PENDING_ENTRY},
        PositionState.PENDING_ENTRY: {
            PositionState.ACTIVE_OPEN,
            PositionState.CANCELLED,
            PositionState.PENDING_EXIT,  # In case of instant abort / kill switch
        },
        PositionState.ACTIVE_OPEN: {
            PositionState.TRAILED_STOP_LOCKED,
            PositionState.TP1_SCALING,
            PositionState.PENDING_EXIT,
        },
        PositionState.TRAILED_STOP_LOCKED: {
            PositionState.PENDING_EXIT,
        },
        PositionState.TP1_SCALING: {
            PositionState.RUNNER_ACTIVE,
            PositionState.PENDING_EXIT,
        },
        PositionState.RUNNER_ACTIVE: {
            PositionState.PENDING_EXIT,
        },
        PositionState.PENDING_EXIT: {
            PositionState.CLOSED,
            PositionState.PENDING_EXIT,  # Retry with adjusted priority fee
        },
        PositionState.CLOSED: set(),      # Terminal state
        PositionState.CANCELLED: set(),   # Terminal state
    }

    def __init__(self, context: Optional[PositionContext] = None):
        self.context = context
        self._state = PositionState.IDLE if context is None else PositionState.PENDING_ENTRY
        self._history: List[StateTransitionRecord] = []
        if context is not None:
            self._history.append(
                StateTransitionRecord(
                    from_state=PositionState.IDLE,
                    to_state=PositionState.PENDING_ENTRY,
                    timestamp=time.time(),
                    trigger="INITIALIZE_PENDING_ENTRY",
                    price_sol=context.entry_price_sol,
                )
            )

    @property
    def current_state(self) -> PositionState:
        return self._state

    @property
    def is_active(self) -> bool:
        return self._state in {
            PositionState.PENDING_ENTRY,
            PositionState.ACTIVE_OPEN,
            PositionState.TRAILED_STOP_LOCKED,
            PositionState.TP1_SCALING,
            PositionState.RUNNER_ACTIVE,
            PositionState.PENDING_EXIT,
        }

    @property
    def is_terminal(self) -> bool:
        return self._state in {PositionState.CLOSED, PositionState.CANCELLED}

    @property
    def history(self) -> List[StateTransitionRecord]:
        return list(self._history)

    def can_transition_to(self, target_state: PositionState) -> bool:
        """Checks whether transition to target_state is permissible."""
        return target_state in self._VALID_TRANSITIONS.get(self._state, set())

    def transition_to(
        self,
        target_state: PositionState,
        trigger: str,
        price_sol: float = 0.0,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> StateTransitionRecord:
        """Executes a valid state transition, recording the event."""
        if not self.can_transition_to(target_state):
            raise IllegalStateTransitionError(
                f"Cannot transition from {self._state.value} to {target_state.value} (Trigger: '{trigger}')"
            )

        # Enforce Path A+ constraint: tranche states are forbidden in Path A+
        if self.context and self.context.mode == ExecutionMode.PATH_A_PLUS:
            if target_state in {PositionState.TP1_SCALING, PositionState.RUNNER_ACTIVE}:
                raise IllegalStateTransitionError(
                    f"Tranche scaling state {target_state.value} forbidden under Invariant 2 Path A+ (100% single exit mode)"
                )

        now = time.time()
        record = StateTransitionRecord(
            from_state=self._state,
            to_state=target_state,
            timestamp=now,
            trigger=trigger,
            price_sol=price_sol,
            metadata=metadata or {},
        )
        self._history.append(record)
        self._state = target_state

        # Update context metrics if available
        if self.context:
            self.context.current_price_sol = price_sol
            if price_sol > self.context.peak_price_sol:
                self.context.peak_price_sol = price_sol

            if target_state == PositionState.CLOSED:
                self.context.exit_price_sol = price_sol
                self.context.exit_timestamp = now
                if self.context.entry_price_sol > 0:
                    self.context.realized_pnl_pct = (
                        (price_sol - self.context.entry_price_sol) / self.context.entry_price_sol
                    ) * 100.0
                    self.context.realized_pnl_sol = (
                        (self.context.size_tokens * price_sol) - (self.context.size_tokens * self.context.entry_price_sol)
                    )

        logger.info(f"Position state changed: {record.from_state.value} -> {record.to_state.value} (Trigger: {trigger})")
        return record

    def update_price_tick(self, current_price_sol: float) -> Tuple[bool, Optional[str]]:
        """
        Updates the current price, ratchets high water mark,
        and determines if profit hurdle or stop loss triggers.
        Returns (should_exit: bool, exit_reason: Optional[str]).
        """
        if not self.is_active or self.context is None:
            return False, None

        ctx = self.context
        ctx.current_price_sol = current_price_sol
        if current_price_sol > ctx.peak_price_sol:
            ctx.peak_price_sol = current_price_sol

        # Check profit hurdle crossing to lock in trailing stop
        if self._state == PositionState.ACTIVE_OPEN:
            if ctx.profit_hurdle_sol > 0 and current_price_sol >= ctx.profit_hurdle_sol:
                # Ratchet stop to lock in +8% net profit
                ctx.stop_price_sol = ctx.entry_price_sol * 1.08
                self.transition_to(
                    PositionState.TRAILED_STOP_LOCKED,
                    trigger="PROFIT_HURDLE_CROSSED",
                    price_sol=current_price_sol,
                    metadata={"ratchet_stop": ctx.stop_price_sol},
                )

        # Dynamic trailing stop update once locked: trail peak by 10%
        if self._state == PositionState.TRAILED_STOP_LOCKED:
            trailing_floor = ctx.peak_price_sol * 0.90
            if trailing_floor > ctx.stop_price_sol:
                ctx.stop_price_sol = trailing_floor

        # Check stop loss hit
        if ctx.stop_price_sol > 0 and current_price_sol <= ctx.stop_price_sol:
            return True, f"STOP_HIT: Price {current_price_sol:.6f} <= Stop {ctx.stop_price_sol:.6f}"

        return False, None
