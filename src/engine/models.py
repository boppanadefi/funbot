"""
Domain models for Paper Trading, Path A+ Position Lifecycle, and Out-of-Sample Validation.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional


class PositionState(str, Enum):
    PENDING_ENTRY = "PENDING_ENTRY"
    OPEN = "OPEN"                      # Active, trailing stop not yet ratcheted
    RATCHETED = "RATCHETED"            # Profit hurdle crossed, stop locked in profit + trailing peak
    CLOSED_TRAIL = "CLOSED_TRAIL"      # Closed by trailing stop trigger
    CLOSED_STOP = "CLOSED_STOP"        # Closed by protective stop (-10%)
    CLOSED_TIMEOUT = "CLOSED_TIMEOUT"  # Closed by 20-minute fallback timeout
    CLOSED_EMERGENCY = "CLOSED_EMERGENCY"  # Closed by emergency risk ceiling


@dataclass
class ExecutionLeg:
    leg_type: str  # "ENTRY" or "EXIT"
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
    ratchet_trigger_price_sol: float
    ratchet_floor_price_sol: float
    trailing_pct: float = 0.12  # 12% trailing distance below peak price
    peak_price_sol: float = 0.0
    current_stop_price_sol: float = 0.0
    state: PositionState = PositionState.OPEN
    ratchet_activated: bool = False
    entry_leg: Optional[ExecutionLeg] = None
    exit_leg: Optional[ExecutionLeg] = None
    exit_time: Optional[datetime] = None
    exit_reason: Optional[str] = None
    realized_net_pnl_usd: float = 0.0
    realized_net_return_pct: float = 0.0

    @property
    def duration_minutes(self) -> float:
        end = self.exit_time or datetime.now()
        return (end - self.entry_time).total_seconds() / 60.0


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
