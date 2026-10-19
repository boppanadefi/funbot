"""
Live vs Paper Walk-Forward Comparative Validator.
Evaluates empirical divergence between simulated PaperLedger trades and
real-world LiveLedger executions to detect model decay or execution drag.
"""

from dataclasses import asdict, dataclass, field
import logging
from typing import Any, Dict, List, Optional, Tuple

from src.engine.paper_ledger import PaperLedger
from src.live.live_ledger import LiveLedgerManager
from src.orchestrator.alerting import AlertDispatcher, AlertEvent, AlertSeverity

logger = logging.getLogger("WalkForwardValidator")

MAX_ACCEPTABLE_SLIPPAGE_VARIANCE_PCT = 2.0  # Falsification gate: max 2% divergence
MIN_ACCEPTABLE_LIVE_WIN_RATE_PCT = 50.0


@dataclass
class DivergenceStatus:
    """Quantitative divergence report between Live and Paper executions."""

    is_acceptable: bool
    slippage_variance_pct: float
    live_avg_slippage_pct: float
    paper_avg_slippage_pct: float
    live_win_rate_pct: float
    paper_win_rate_pct: float
    win_rate_delta_pct: float
    live_trade_count: int
    paper_trade_count: int
    live_net_pnl_usd: float
    paper_net_pnl_usd: float
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class WalkForwardValidator:
    """
    Validates live production execution fidelity against the simulated paper baseline.
    """

    def __init__(
        self,
        live_ledger: Optional[LiveLedgerManager] = None,
        paper_ledger: Optional[PaperLedger] = None,
        alerts: Optional[AlertDispatcher] = None,
        max_slippage_variance_pct: float = MAX_ACCEPTABLE_SLIPPAGE_VARIANCE_PCT,
    ):
        self.live_ledger = live_ledger or LiveLedgerManager()
        self.paper_ledger = paper_ledger or PaperLedger()
        self.alerts = alerts or AlertDispatcher()
        self.max_slippage_variance_pct = max_slippage_variance_pct

    def compute_divergence(self) -> DivergenceStatus:
        """
        Calculates divergence between paper trading model and live on-chain executions.
        """
        live_agg = self.live_ledger.get_aggregate_metrics()
        live_count = live_agg["total_trades"]
        live_win_rate = live_agg["win_rate_pct"]
        live_avg_slip = live_agg["avg_slippage_pct"]
        live_pnl_usd = live_agg["total_net_pnl_usd"]

        # Pull paper trades from PaperLedger
        paper_trades = getattr(self.paper_ledger, "get_all_trades", lambda: [])()[:500]
        paper_count = len(paper_trades)

        if paper_count > 0:
            paper_wins = sum(1 for t in paper_trades if t.get("net_pnl_sol", 0.0) > 0)
            paper_win_rate = round((paper_wins / paper_count) * 100.0, 2)
            paper_pnl_usd = sum(t.get("net_pnl_usd", 0.0) for t in paper_trades)
            # Default simulated baseline slippage is ~0.80% (two-way)
            paper_avg_slip = 0.80
        else:
            paper_win_rate = 60.0
            paper_avg_slip = 0.80
            paper_pnl_usd = 0.0

        slippage_variance = abs(live_avg_slip - paper_avg_slip) if live_count > 0 else 0.0
        win_rate_delta = (live_win_rate - paper_win_rate) if live_count > 0 else 0.0

        warnings: List[str] = []
        is_acceptable = True

        if live_count >= 3:
            # 1. Slippage Variance Gate (< 2.0%)
            if slippage_variance > self.max_slippage_variance_pct:
                is_acceptable = False
                warn = (
                    f"Slippage variance exceeded: Live average slippage ({live_avg_slip:.2f}%) "
                    f"diverges from paper model ({paper_avg_slip:.2f}%) by {slippage_variance:.2f}% "
                    f"(Threshold: <={self.max_slippage_variance_pct:.2f}%)."
                )
                warnings.append(warn)
                logger.warning(warn)

            # 2. Win Rate Degradation Gate
            if live_win_rate < MIN_ACCEPTABLE_LIVE_WIN_RATE_PCT:
                is_acceptable = False
                warn = (
                    f"Live win rate collapse: {live_win_rate:.1f}% is below minimum "
                    f"threshold ({MIN_ACCEPTABLE_LIVE_WIN_RATE_PCT:.1f}%)."
                )
                warnings.append(warn)
                logger.warning(warn)

        return DivergenceStatus(
            is_acceptable=is_acceptable,
            slippage_variance_pct=round(slippage_variance, 4),
            live_avg_slippage_pct=round(live_avg_slip, 4),
            paper_avg_slippage_pct=round(paper_avg_slip, 4),
            live_win_rate_pct=round(live_win_rate, 2),
            paper_win_rate_pct=round(paper_win_rate, 2),
            win_rate_delta_pct=round(win_rate_delta, 2),
            live_trade_count=live_count,
            paper_trade_count=paper_count,
            live_net_pnl_usd=round(live_pnl_usd, 2),
            paper_net_pnl_usd=round(paper_pnl_usd, 2),
            warnings=warnings,
        )

    async def check_and_alert(self) -> DivergenceStatus:
        """Evaluates divergence and dispatches webhook alerts if gates are violated."""
        status = self.compute_divergence()
        if not status.is_acceptable and status.warnings:
            await self.alerts.dispatch(
                AlertEvent(
                    event_type="WALK_FORWARD_DIVERGENCE_ALERT",
                    severity=AlertSeverity.WARNING,
                    title="⚠️ Walk-Forward Divergence Alert",
                    message="\n".join(status.warnings),
                    metadata=status.to_dict(),
                )
            )
        return status
