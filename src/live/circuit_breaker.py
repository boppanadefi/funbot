"""
Daily Circuit Breaker & Pre-Trade Risk Capacity Monitor — Guardrail 7.
Enforces the -$10.00 daily loss ceiling and pre-trade capacity checks.
"""

from datetime import datetime, timezone
import json
import logging
from pathlib import Path
from typing import Dict, Optional, Tuple

logger = logging.getLogger("CircuitBreaker")


class DailyLossExceededError(Exception):
    """Raised when realized losses have reached or exceeded the daily limit."""
    pass


class InsufficientRiskCapacityError(Exception):
    """Raised when a proposed trade's stressed loss exceeds remaining daily risk budget."""
    pass


class DailyCircuitBreaker:
    """
    Monitors daily cumulative loss against the -$10.00 ceiling and enforces
    pre-trade risk capacity checks before opening any position.
    """

    MAX_DAILY_LOSS_USD: float = 10.00
    MANDATORY_EXIT_RESERVE_USD: float = 3.00  # ~0.02 SOL emergency exit reserve

    def __init__(self, state_file_path: str = "data/live/circuit_breaker_state.json"):
        self.state_file = Path(state_file_path)
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self._load_or_init()

    def _today_utc(self) -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def _load_or_init(self) -> None:
        today = self._today_utc()
        if not self.state_file.exists():
            default_state = {
                "date": today,
                "realized_daily_loss_usd": 0.0,
                "realized_daily_gain_usd": 0.0,
                "net_daily_pnl_usd": 0.0,
                "is_halted": False,
                "halt_reason": None,
                "operator_signoff_required": False,
                "trades_count": 0,
            }
            self._save(default_state)

    def _read(self) -> Dict:
        if not self.state_file.exists():
            self._load_or_init()
        try:
            with open(self.state_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                today = self._today_utc()
                # Check for day rollover at 00:00 UTC
                if data.get("date") != today:
                    logger.info("New UTC day detected (%s -> %s). Rolling over circuit breaker state.", data.get("date"), today)
                    # If halted yesterday, preserve halt if operator sign-off required
                    was_halted = data.get("is_halted", False)
                    data = {
                        "date": today,
                        "realized_daily_loss_usd": 0.0,
                        "realized_daily_gain_usd": 0.0,
                        "net_daily_pnl_usd": 0.0,
                        "is_halted": was_halted,  # If halted, require operator reset
                        "halt_reason": data.get("halt_reason") if was_halted else None,
                        "operator_signoff_required": was_halted,
                        "trades_count": 0,
                    }
                    self._save(data)
                return data
        except Exception as e:
            logger.error("Error reading circuit breaker state: %s", e)
            return {
                "date": self._today_utc(),
                "realized_daily_loss_usd": 10.00,
                "is_halted": True,
                "halt_reason": f"State read error: {e}",
                "operator_signoff_required": True,
            }

    def _save(self, state: Dict) -> None:
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_file.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
        tmp.replace(self.state_file)

    def get_remaining_budget(self) -> float:
        """Returns the remaining loss budget in USD before hitting the -$10.00 ceiling."""
        state = self._read()
        net_loss = max(0.0, -state.get("net_daily_pnl_usd", 0.0))
        remaining = max(0.0, self.MAX_DAILY_LOSS_USD - net_loss)
        return round(remaining, 2)

    def record_trade_pnl(self, net_pnl_usd: float) -> Dict:
        """Records a realized trade outcome and trips circuit breaker if -$10.00 is breached."""
        state = self._read()
        net_pnl_usd = round(net_pnl_usd, 4)
        state["trades_count"] = state.get("trades_count", 0) + 1

        if net_pnl_usd >= 0:
            state["realized_daily_gain_usd"] = round(state.get("realized_daily_gain_usd", 0.0) + net_pnl_usd, 4)
        else:
            state["realized_daily_loss_usd"] = round(state.get("realized_daily_loss_usd", 0.0) + abs(net_pnl_usd), 4)

        current_net = round(state.get("realized_daily_gain_usd", 0.0) - state.get("realized_daily_loss_usd", 0.0), 4)
        state["net_daily_pnl_usd"] = current_net

        # Check circuit breaker trigger: net loss reaches -$10.00
        if current_net <= -self.MAX_DAILY_LOSS_USD:
            state["is_halted"] = True
            state["halt_reason"] = f"Daily net loss reached ${abs(current_net):.2f} (Ceiling: -${self.MAX_DAILY_LOSS_USD:.2f})"
            state["operator_signoff_required"] = True
            logger.critical("CIRCUIT BREAKER TRIPPED: %s", state["halt_reason"])

        self._save(state)
        return state

    def check_preflight_capacity(
        self,
        position_notional_usd: float,
        stop_loss_pct: float = 25.0,
        gap_penalty_pct: float = 10.0,
        sol_price_usd: float = 155.0,
    ) -> Tuple[bool, str, float]:
        """
        Validates whether proposed position fits within remaining daily risk budget.
        Formula: Stressed Loss + Exit Fee Reserve <= Remaining Budget
        """
        state = self._read()
        if state.get("is_halted", False):
            return False, f"Trading halted by Circuit Breaker: {state.get('halt_reason')}", 0.0

        remaining_budget = self.get_remaining_budget()

        # Compute stressed loss
        total_stress_pct = stop_loss_pct + gap_penalty_pct
        stressed_loss_usd = round(position_notional_usd * (total_stress_pct / 100.0), 2)
        exit_reserve_usd = round(self.MANDATORY_EXIT_RESERVE_USD, 2)
        total_risk_exposure = round(stressed_loss_usd + exit_reserve_usd, 2)

        if total_risk_exposure > remaining_budget:
            msg = (
                f"Proposed trade exposure (${total_risk_exposure:.2f} = ${stressed_loss_usd:.2f} stress + "
                f"${exit_reserve_usd:.2f} exit reserve) exceeds remaining daily budget (${remaining_budget:.2f})."
            )
            return False, msg, total_risk_exposure

        return True, "Pre-trade risk capacity OK", total_risk_exposure

    def reset_circuit_breaker(self, operator: str, notes: str = "Operator daily sign-off") -> Dict:
        """Manually unlocks circuit breaker following operator review."""
        state = self._read()
        state["is_halted"] = False
        state["halt_reason"] = None
        state["operator_signoff_required"] = False
        state["last_reset_by"] = operator
        state["last_reset_ts"] = datetime.now(timezone.utc).isoformat()
        state["last_reset_notes"] = notes
        self._save(state)
        logger.info("Circuit Breaker RESET by operator %s: %s", operator, notes)
        return state

    def get_status(self) -> Dict:
        """Returns full circuit breaker status report."""
        state = self._read()
        state["remaining_budget_usd"] = self.get_remaining_budget()
        return state

    @property
    def is_halted(self) -> bool:
        """Returns whether circuit breaker is currently tripped."""
        return bool(self._read().get("is_halted", False))

    @property
    def net_daily_pnl(self) -> float:
        """Returns net realized daily PnL in USD."""
        return float(self._read().get("net_daily_pnl_usd", 0.0))
