"""
Pre-Trade Risk Capacity Monitor & Daily Circuit Breaker.
Enforces Guardrail 7: -$10.00 daily loss ceiling, pre-trade stress capacity check, and 1-position concurrency.
"""

from datetime import datetime, timezone
from typing import Dict, List, Optional
from src.engine.models import PaperPosition


class DailyRiskMonitor:
    """
    Monitors portfolio risk budgets, daily loss capacity, and active position limits.
    """

    DAILY_LOSS_CEILING_USD = 10.00  # Hard circuit breaker at -$10.00 daily loss
    MAX_CONCURRENT_POSITIONS = 1    # Strictly 1 active position at a time

    def __init__(self, daily_loss_ceiling_usd: float = 10.00):
        self.daily_loss_ceiling_usd = daily_loss_ceiling_usd
        self.active_positions: Dict[str, PaperPosition] = {}
        self.realized_pnl_today_usd: float = 0.0
        self.current_day: str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def _check_and_reset_day(self) -> None:
        today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if today_str != self.current_day:
            self.current_day = today_str
            self.realized_pnl_today_usd = 0.0

    def can_open_position(
        self,
        position_notional_usd: float,
        stress_loss_estimate_usd: float,
    ) -> bool:
        """
        Guardrail 7: Verifies Stressed Loss + Exit Reserve fits into the remaining $10 daily budget.
        Also enforces max 1 concurrent active position.
        """
        self._check_and_reset_day()

        # Check 1: Concurrency constraint
        if len(self.active_positions) >= self.MAX_CONCURRENT_POSITIONS:
            return False

        # Check 2: Circuit breaker active
        if self.realized_pnl_today_usd <= -self.daily_loss_ceiling_usd:
            return False

        # Check 3: Pre-trade stressed loss capacity
        potential_total_loss = abs(min(0.0, self.realized_pnl_today_usd)) + stress_loss_estimate_usd
        if potential_total_loss > self.daily_loss_ceiling_usd:
            return False

        return True

    def register_open_position(self, pos: PaperPosition) -> None:
        self.active_positions[pos.position_id] = pos

    def register_closed_position(self, pos: PaperPosition) -> None:
        self.active_positions.pop(pos.position_id, None)
        self.realized_pnl_today_usd += pos.realized_net_pnl_usd

    @property
    def remaining_daily_risk_budget_usd(self) -> float:
        self._check_and_reset_day()
        consumed_loss = abs(min(0.0, self.realized_pnl_today_usd))
        return max(0.0, round(self.daily_loss_ceiling_usd - consumed_loss, 2))

    @property
    def is_circuit_breaker_tripped(self) -> bool:
        self._check_and_reset_day()
        return self.realized_pnl_today_usd <= -self.daily_loss_ceiling_usd
