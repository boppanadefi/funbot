"""
28-Day Autonomous Live Performance Scorecard & Go/No-Go Production Gate.
Evaluates empirical on-chain executions against quantitative sustainability criteria.
"""

from dataclasses import asdict, dataclass, field
import logging
import time
from typing import Any, Dict, List, Optional

from src.live.live_ledger import LiveLedgerManager, LiveTradeRecord
from src.live.walk_forward_validator import WalkForwardValidator

logger = logging.getLogger("LiveScorecard")

EVALUATION_WINDOW_DAYS = 28.0
MAX_DRAWDOWN_LIMIT_USD = 10.00
MIN_WIN_RATE_TARGET_PCT = 60.0
MAX_SLIPPAGE_VARIANCE_PCT = 2.0
ACTIVE_BANKROLL_BASE_USD = 25.00


@dataclass
class ScorecardCriterion:
    """Individual quantitative criterion evaluation."""

    name: str
    target: str
    actual: str
    is_passed: bool
    implication_if_failed: str
    raw_actual: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class LiveScorecardReport:
    """Consolidated 28-day live execution scorecard."""

    timestamp: float
    window_days: float
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate_pct: float
    net_pnl_sol: float
    net_pnl_usd: float
    return_on_bankroll_pct: float
    max_drawdown_usd: float
    profit_factor: float
    avg_slippage_pct: float
    is_go_approved: bool
    verdict: str
    criteria: List[ScorecardCriterion] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class LiveScorecard:
    """
    Computes rolling 28-day production performance metrics and Go/No-Go validation.
    """

    def __init__(
        self,
        live_ledger: Optional[LiveLedgerManager] = None,
        validator: Optional[WalkForwardValidator] = None,
        bankroll_usd: float = ACTIVE_BANKROLL_BASE_USD,
        window_days: float = EVALUATION_WINDOW_DAYS,
    ):
        self.live_ledger = live_ledger or LiveLedgerManager()
        self.validator = validator or WalkForwardValidator(live_ledger=self.live_ledger)
        self.bankroll_usd = bankroll_usd
        self.window_days = window_days

    def generate_report(self) -> LiveScorecardReport:
        """Evaluates live trade records against Milestone 3 quantitative criteria."""
        now = time.time()
        window_sec = self.window_days * 86400.0
        cutoff_ts = now - window_sec

        # Fetch closed live trades within window
        all_closed = self.live_ledger.get_recent_live_trades(limit=1000, status="CLOSED")
        trades = [t for t in all_closed if t.entry_timestamp >= cutoff_ts]

        total = len(trades)
        if total == 0:
            return self._build_empty_report(now)

        winners = [t for t in trades if t.net_pnl_sol > 0]
        losers = [t for t in trades if t.net_pnl_sol <= 0]
        win_rate = (len(winners) / total) * 100.0

        net_pnl_sol = sum(t.net_pnl_sol for t in trades)
        net_pnl_usd = sum(t.net_pnl_usd for t in trades)
        return_on_bankroll = (net_pnl_usd / self.bankroll_usd) * 100.0

        # Calculate max drawdown
        cumulative_pnl = 0.0
        peak_pnl = 0.0
        max_dd_usd = 0.0
        gross_profit = 0.0
        gross_loss = 0.0

        for t in reversed(trades):  # Chronological order
            cumulative_pnl += t.net_pnl_usd
            if cumulative_pnl > peak_pnl:
                peak_pnl = cumulative_pnl
            dd = peak_pnl - cumulative_pnl
            if dd > max_dd_usd:
                max_dd_usd = dd

            if t.net_pnl_usd > 0:
                gross_profit += t.net_pnl_usd
            else:
                gross_loss += abs(t.net_pnl_usd)

        profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else (999.0 if gross_profit > 0 else 1.0)
        avg_slippage = sum(t.total_slippage_pct for t in trades) / total

        # Divergence evaluation
        divergence = self.validator.compute_divergence()

        # Build Criteria Evaluations
        c1 = ScorecardCriterion(
            name="Net Expectancy",
            target="> 0.0 SOL",
            actual=f"{net_pnl_sol:+.5f} SOL (${net_pnl_usd:+.2f})",
            is_passed=bool(net_pnl_sol > 0.0),
            implication_if_failed="Strategy lacks net edge after on-chain friction; halt execution.",
            raw_actual=net_pnl_sol,
        )

        c2 = ScorecardCriterion(
            name="Win Rate Target",
            target=f">= {MIN_WIN_RATE_TARGET_PCT:.1f}%",
            actual=f"{win_rate:.1f}% ({len(winners)}/{total})",
            is_passed=bool(win_rate >= MIN_WIN_RATE_TARGET_PCT),
            implication_if_failed="Friction buster failing; execution drag is bleeding bankroll.",
            raw_actual=win_rate,
        )

        c3 = ScorecardCriterion(
            name="Max Drawdown Ceiling",
            target=f"< ${MAX_DRAWDOWN_LIMIT_USD:.2f}",
            actual=f"${max_dd_usd:.2f}",
            is_passed=bool(max_dd_usd < MAX_DRAWDOWN_LIMIT_USD),
            implication_if_failed="Daily risk allocation breached; stops failing under cascade.",
            raw_actual=max_dd_usd,
        )

        c4 = ScorecardCriterion(
            name="Slippage Variance Tolerance",
            target=f"< {MAX_SLIPPAGE_VARIANCE_PCT:.1f}%",
            actual=f"{divergence.slippage_variance_pct:.2f}%",
            is_passed=bool(divergence.slippage_variance_pct < MAX_SLIPPAGE_VARIANCE_PCT),
            implication_if_failed="Real-world AMM execution diverges unsustainably from paper model.",
            raw_actual=divergence.slippage_variance_pct,
        )

        c5 = ScorecardCriterion(
            name="Active Bankroll Return",
            target="> 0.0%",
            actual=f"{return_on_bankroll:+.1f}%",
            is_passed=bool(return_on_bankroll > 0.0),
            implication_if_failed="Negative capital growth on active $25 allocation.",
            raw_actual=return_on_bankroll,
        )

        criteria = [c1, c2, c3, c4, c5]
        all_passed = all(c.is_passed for c in criteria)

        if all_passed:
            verdict = "GO — APPROVED FOR FULL AUTONOMOUS SCALE"
        else:
            failed_names = [c.name for c in criteria if not c.is_passed]
            verdict = f"NO-GO — GATES FAILED: {', '.join(failed_names)}"

        return LiveScorecardReport(
            timestamp=now,
            window_days=self.window_days,
            total_trades=total,
            winning_trades=len(winners),
            losing_trades=len(losers),
            win_rate_pct=round(win_rate, 2),
            net_pnl_sol=round(net_pnl_sol, 6),
            net_pnl_usd=round(net_pnl_usd, 2),
            return_on_bankroll_pct=round(return_on_bankroll, 2),
            max_drawdown_usd=round(max_dd_usd, 2),
            profit_factor=round(profit_factor, 2),
            avg_slippage_pct=round(avg_slippage, 4),
            is_go_approved=all_passed,
            verdict=verdict,
            criteria=criteria,
        )

    def _build_empty_report(self, timestamp: float) -> LiveScorecardReport:
        return LiveScorecardReport(
            timestamp=timestamp,
            window_days=self.window_days,
            total_trades=0,
            winning_trades=0,
            losing_trades=0,
            win_rate_pct=0.0,
            net_pnl_sol=0.0,
            net_pnl_usd=0.0,
            return_on_bankroll_pct=0.0,
            max_drawdown_usd=0.0,
            profit_factor=1.0,
            avg_slippage_pct=0.0,
            is_go_approved=False,
            verdict="NO-GO — NO COMPLETED LIVE TRADES IN 28-DAY WINDOW",
            criteria=[
                ScorecardCriterion("Net Expectancy", "> 0.0 SOL", "0.00000 SOL", False, "No trades recorded."),
                ScorecardCriterion("Win Rate Target", ">= 60.0%", "0.0%", False, "No trades recorded."),
                ScorecardCriterion("Max Drawdown Ceiling", "< $10.00", "$0.00", True, "Within budget limit."),
                ScorecardCriterion("Slippage Variance Tolerance", "< 2.0%", "0.00%", True, "No variance recorded."),
                ScorecardCriterion("Active Bankroll Return", "> 0.0%", "0.0%", False, "No return generated."),
            ],
        )
