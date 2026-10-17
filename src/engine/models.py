"""
Domain models for Paper Trading, Path A+ Position Lifecycle, and Out-of-Sample Validation.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional


class PositionState(str, Enum):
    PENDING_ENTRY = "PENDING_ENTRY"
    OPEN = "OPEN"                      # Active, 100% held, TP1 not yet hit
    RUNNER_ACTIVE = "RUNNER_ACTIVE"    # TP1 executed: 90% sold, 10% lottery runner active
    RATCHETED = "RATCHETED"            # Kept for backward compatibility
    CLOSED_TRAIL = "CLOSED_TRAIL"      # Closed by trailing stop trigger (structure or peak trail)
    CLOSED_STOP = "CLOSED_STOP"        # Closed by protective stop (-10%)
    CLOSED_TIMEOUT = "CLOSED_TIMEOUT"  # Closed by 20-minute fallback timeout
    CLOSED_EMERGENCY = "CLOSED_EMERGENCY"  # Closed by emergency risk ceiling
    CLOSED_TIME_DECAY = "CLOSED_TIME_DECAY"        # Fix 3: Momentum time-decay (2 candles no high + >40% vol drop)
    CLOSED_LIQUIDITY_DRAIN = "CLOSED_LIQUIDITY_DRAIN"  # Fix 3: Liquidity collapse (simulated sell slippage > 5%)
    CLOSED_RUNNER_TIME_KILL = "CLOSED_RUNNER_TIME_KILL"  # Fix 5: Runner hard time kill (<+30% gross within 3m of TP1)

    @property
    def is_closed(self) -> bool:
        return self in (
            PositionState.CLOSED_TRAIL,
            PositionState.CLOSED_STOP,
            PositionState.CLOSED_TIMEOUT,
            PositionState.CLOSED_EMERGENCY,
            PositionState.CLOSED_TIME_DECAY,
            PositionState.CLOSED_LIQUIDITY_DRAIN,
            PositionState.CLOSED_RUNNER_TIME_KILL,
        )


@dataclass
class ExecutionLeg:
    leg_type: str  # "ENTRY", "TP1_EXIT", "RUNNER_EXIT", or "EXIT"
    timestamp: datetime
    price_sol: float
    sol_amount: float
    token_amount: float
    venue_fee_sol: float
    jito_tip_sol: float
    priority_fee_sol: float
    base_fee_sol: float = 0.000005

    @property
    def total_friction_sol(self) -> float:
        return self.venue_fee_sol + self.jito_tip_sol + self.priority_fee_sol + self.base_fee_sol


@dataclass
class PaperPosition:
    position_id: str
    pool_address: str
    token_mint: str
    regime: str
    entry_time: datetime
    entry_price_sol: float
    tokens_held: float
    sol_spent: float
    position_notional_usd: float
    # Friction-Busting 90/10 Scale-Out (Invariant 2) Parameters:
    tp1_target_gross_pct: float = 0.09     # +9.0% gross target (+8% to +10% range)
    tp1_price_sol: float = 0.0             # Entry * (1 + tp1_target_gross_pct)
    tp1_size_pct: float = 0.90             # 90% sold at TP1 to lock net profit after friction
    runner_size_pct: float = 0.10          # 10% retained as lottery runner
    runner_tokens_held: float = 0.0        # Remaining tokens held during runner stage
    runner_trailing_pct: float = 0.15      # 15% wide trailing stop on runner
    protective_stop_loss_pct: float = 0.10 # Initial protective stop at -10%
    peak_price_sol: float = 0.0
    current_stop_price_sol: float = 0.0
    state: PositionState = PositionState.OPEN
    tp1_activated: bool = False
    tp1_time: Optional[datetime] = None    # Timestamp when TP1 executed
    tp1_leg: Optional[ExecutionLeg] = None
    runner_leg: Optional[ExecutionLeg] = None
    tp1_realized_usd: float = 0.0
    runner_realized_usd: float = 0.0
    # Hard Time Kill on Runner (Fix 5):
    runner_hard_kill_timeout_minutes: float = 3.0  # Must achieve +30% gross within 3 minutes of TP1
    runner_target_gross_pct: float = 0.30          # +30% gross target hurdle for runner
    runner_target_achieved: bool = False
    # Time-Sliced TP1 (Anti-MEV / Sandwich Defense):
    tp1_slices_total: int = 3              # Split 90% TP1 tranche into 3 equal sells (30% each)
    tp1_slices_executed: int = 0           # Count of completed slices
    tp1_legs: List[ExecutionLeg] = field(default_factory=list) # Slices spaced 2-3 blocks apart
    tp1_slice_interval_seconds: float = 1.0 # 2-3 blocks (~800ms - 1.2s)
    entry_leg: Optional[ExecutionLeg] = None
    exit_leg: Optional[ExecutionLeg] = None
    exit_time: Optional[datetime] = None
    exit_reason: Optional[str] = None
    realized_net_pnl_usd: float = 0.0
    realized_net_return_pct: float = 0.0
    # Fix 3: Local-Structure & Time-Decay Exit attributes:
    entry_breakout_volume_usd: float = 0.0
    structure_stop_price_sol: float = 0.0
    lowest_low_2m_candles_sol: float = 0.0
    last_liquidity_check_time: Optional[datetime] = None
    trailing_mode: str = "STRUCTURE"  # "STRUCTURE" (candle lows) or "PERCENTAGE" (peak trail)
    # Backward compatibility attributes:
    ratchet_trigger_price_sol: float = 0.0
    ratchet_floor_price_sol: float = 0.0
    trailing_pct: float = 0.15
    ratchet_activated: bool = False

    @property
    def duration_minutes(self) -> float:
        from datetime import timezone
        end = self.exit_time or datetime.now(timezone.utc)
        if self.entry_time.tzinfo is None and end.tzinfo is not None:
            end = datetime.now()
        elif self.entry_time.tzinfo is not None and end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)
        return (end - self.entry_time).total_seconds() / 60.0

    @property
    def is_runner_active(self) -> bool:
        return self.tp1_activated and self.state == PositionState.RUNNER_ACTIVE

    @property
    def is_closed(self) -> bool:
        return self.state.is_closed


@dataclass
class TradeOutcome:
    position_id: str
    is_win: bool
    net_pnl_usd: float
    net_return_pct: float
    exit_reason: str
    ratchet_activated: bool


@dataclass
class ValidationReport:
    dataset_type: str  # "IN_SAMPLE" or "OUT_OF_SAMPLE"
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate_pct: float
    gross_pnl_usd: float
    total_friction_usd: float
    net_pnl_usd: float
    net_expectancy_per_trade_usd: float
    profit_factor: float
    max_drawdown_usd: float
    is_gate_passed: bool
    rejection_reasons: List[str] = field(default_factory=list)
