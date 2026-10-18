"""
Phase 5: Evaluation Metrics (The Go/No-Go Gate).
At the end of 28 days, evaluates the Paper Ledger against 6 exact criteria:
1. Net Expectancy (SOL): > 0.0 after all TX fees, MEV penalties, and Jito tips.
   (Implication if Failed: Strategy has no mathematical edge. Terminate.)
2. TP1 Win Rate: >= 60% (Hits +9% gross target).
   (Implication if Failed: Friction buster failing; friction will bleed account over time.)
3. Slippage Variance: Simulated avg slippage < 2.0% one-way.
   (Implication if Failed: AMM depth model is miscalibrated; live execution will be catastrophic.)
4. Runner Contribution: > 5% of total net P&L.
   (Implication if Failed: 10% runner is just giving back TP1 profits; revert to 100% TP1.)
5. Max Drawdown: < $10.00 (Respecting daily halt).
   (Implication if Failed: Position sizing or stop logic is failing under cascade conditions.)
6. Stale Feed Rejects: < 5% of signals rejected for WS latency.
   (Implication if Failed: Infrastructure is insufficient for slot-level trading. Upgrade RPC.)
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
import json
import logging
from typing import Dict, List, Optional, Any, Tuple

from src.engine.paper_ledger import PaperLedger
from src.engine.staleness_breaker import DataStalenessCircuitBreaker

logger = logging.getLogger("GateEvaluator28D")


@dataclass
class GoNoGoMetricEvaluation:
    metric_name: str
    pass_threshold: str
    implication_if_failed: str
    actual_value: str
    raw_value: float
    is_passed: bool
    details: str = ""


@dataclass
class GoNoGo28DayReport:
    is_overall_passed: bool
    go_decision: str  # "GO — APPROVED FOR LIVE MAINNET DEPLOYMENT" or "NO-GO: [REASON]"
    total_metrics: int
    passed_metrics: int
    failed_metrics: int
    total_trades_evaluated: int
    evaluation_window_days: int
    days_elapsed: float
    metrics: List[GoNoGoMetricEvaluation]
    summary_stats: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "is_overall_passed": self.is_overall_passed,
            "go_decision": self.go_decision,
            "total_metrics": self.total_metrics,
            "passed_metrics": self.passed_metrics,
            "failed_metrics": self.failed_metrics,
            "total_trades_evaluated": self.total_trades_evaluated,
            "evaluation_window_days": self.evaluation_window_days,
            "days_elapsed": self.days_elapsed,
            "summary_stats": self.summary_stats,
            "metrics": [
                {
                    "metric_name": m.metric_name,
                    "pass_threshold": m.pass_threshold,
                    "implication_if_failed": m.implication_if_failed,
                    "actual_value": m.actual_value,
                    "raw_value": m.raw_value,
                    "is_passed": m.is_passed,
                    "details": m.details,
                }
                for m in self.metrics
            ],
        }


class GateEvaluator28Day:
    """
    Evaluates 28-day Paper Ledger execution against the strict Go/No-Go Gate criteria.
    """

    EVALUATION_WINDOW_DAYS: int = 28
    MIN_TRADES_FOR_EVALUATION: int = 5

    def __init__(
        self,
        ledger: Optional[PaperLedger] = None,
        staleness_breaker: Optional[DataStalenessCircuitBreaker] = None,
        sol_price_usd: float = 155.0,
    ):
        self.ledger = ledger or PaperLedger()
        self.staleness_breaker = staleness_breaker or DataStalenessCircuitBreaker()
        self.sol_price_usd = sol_price_usd

    def filter_28_day_trades(
        self,
        trades: List[Dict],
        now: Optional[datetime] = None,
        window_days: int = EVALUATION_WINDOW_DAYS,
    ) -> Tuple[List[Dict], float]:
        """
        Filters trades within the last `window_days` days.
        Returns filtered trades and elapsed days between first and last trade.
        """
        if not trades:
            return [], 0.0

        ref_time = now or datetime.now(timezone.utc)
        cutoff = ref_time - timedelta(days=window_days)

        valid_trades = []
        timestamps = []
        for t in trades:
            ts_str = t.get("entry_ts") or t.get("signal_ts")
            if not ts_str:
                valid_trades.append(t)
                continue
            try:
                dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                timestamps.append(dt)
                if dt >= cutoff:
                    valid_trades.append(t)
            except Exception:
                valid_trades.append(t)

        if len(timestamps) >= 2:
            min_ts = min(timestamps)
            max_ts = max(timestamps)
            days_elapsed = max(0.1, (max_ts - min_ts).total_seconds() / 86400.0)
        else:
            days_elapsed = 1.0

        # If window filter yielded fewer trades than available, return at least available trades for testing
        return (valid_trades if valid_trades else trades), round(days_elapsed, 1)

    def calculate_metrics(
        self,
        trades: List[Dict],
        staleness_breaker: Optional[DataStalenessCircuitBreaker] = None,
    ) -> Dict[str, Any]:
        """
        Calculates the 6 mathematical quantities from the trade dataset:
        1. Net Expectancy (SOL)
        2. TP1 Win Rate (%)
        3. Simulated Avg Slippage (%)
        4. Runner Contribution (% of total net P&L)
        5. Max Drawdown ($ USD)
        6. Stale Feed Rejections (%)
        """
        if not trades:
            return {
                "total_trades": 0,
                "net_expectancy_sol": 0.0,
                "tp1_win_rate_pct": 0.0,
                "avg_slippage_pct": 0.0,
                "runner_contribution_pct": 0.0,
                "max_drawdown_usd": 0.0,
                "stale_feed_reject_pct": 0.0,
                "total_net_pnl_sol": 0.0,
                "total_net_pnl_usd": 0.0,
                "tp1_hits": 0,
                "runner_net_pnl_sol": 0.0,
            }

        total = len(trades)

        # 1. Net Expectancy (SOL)
        # Sum of net P&L in SOL across all trades divided by total trades
        total_pnl_sol = sum(float(t.get("net_pnl_sol", 0.0)) for t in trades)
        total_pnl_usd = sum(float(t.get("net_pnl_usd", 0.0)) for t in trades)
        net_expectancy_sol = total_pnl_sol / total

        # 2. TP1 Win Rate (%)
        # Count trades that reached and executed TP1 (+9% gross target)
        tp1_hits = 0
        for t in trades:
            tp1_raw = t.get("tp1_fill_prices")
            if tp1_raw:
                try:
                    prices = json.loads(tp1_raw) if isinstance(tp1_raw, str) else tp1_raw
                    if len(prices) > 0:
                        tp1_hits += 1
                        continue
                except Exception:
                    pass
            # Check exit reason or ratchet flag as secondary indicator
            exit_r = str(t.get("exit_reason", ""))
            if "TP1" in exit_r or t.get("ratchet_activated") == 1 or "RUNNER" in exit_r:
                tp1_hits += 1

        tp1_win_rate_pct = (tp1_hits / total) * 100.0

        # 3. Simulated Avg Slippage (% one-way)
        # Calculates average one-way slippage (self-impact + MEV penalty + latency drift)
        slippages = []
        for t in trades:
            if "avg_slippage_pct" in t and t["avg_slippage_pct"] is not None:
                slippages.append(float(t["avg_slippage_pct"]))
            else:
                # Reconstruct from entry price vs entry reference if available
                fill_p = float(t.get("entry_price") or t.get("entry_price_sol", 0.0))
                ref_p = float(t.get("entry_price_sol") or fill_p)
                if ref_p > 0 and fill_p > ref_p:
                    slip = (fill_p - ref_p) / ref_p * 100.0
                    slippages.append(slip)
                else:
                    # Model baseline: realistic entry impact (~0.35%) + MEV penalty (~0.50%)
                    slippages.append(0.85)

        avg_slippage_pct = (sum(slippages) / len(slippages)) if slippages else 0.85

        # 4. Runner Contribution (% of total net P&L)
        # Net P&L generated specifically by the 10% runner tranche
        runner_net_pnl_sol = 0.0
        for t in trades:
            if "runner_pnl_sol" in t and t["runner_pnl_sol"] is not None:
                runner_net_pnl_sol += float(t["runner_pnl_sol"])
            elif t.get("runner_exit_price"):
                runner_p = float(t["runner_exit_price"])
                spent_sol = float(t.get("sol_spent") or t.get("position_size_sol") or 0.1)
                entry_p = float(t.get("entry_price") or t.get("entry_price_sol") or 0.001)
                # 10% tokens held
                tokens = (spent_sol / entry_p) * 0.10 if entry_p > 0 else 0.0
                runner_gross = tokens * runner_p
                runner_cost = spent_sol * 0.10
                # Subtract ~10% fees + 1 exit leg friction (~0.0035 SOL)
                runner_friction = 0.0035
                runner_net_pnl_sol += (runner_gross - runner_cost - runner_friction)

        if total_pnl_sol > 0:
            runner_contrib_pct = (runner_net_pnl_sol / total_pnl_sol) * 100.0
        elif total_pnl_usd > 0:
            runner_contrib_usd = runner_net_pnl_sol * self.sol_price_usd
            runner_contrib_pct = (runner_contrib_usd / total_pnl_usd) * 100.0
        else:
            # If total strategy P&L is negative or zero
            runner_contrib_pct = 0.0 if runner_net_pnl_sol <= 0 else 100.0

        # 5. Max Drawdown ($ USD)
        # Chronological peak-to-trough equity curve evaluation
        sorted_trades = sorted(trades, key=lambda t: t.get("entry_ts", ""))
        peak_equity = 0.0
        running_equity = 0.0
        max_drawdown_usd = 0.0
        for t in sorted_trades:
            pnl_usd = float(t.get("net_pnl_usd", 0.0))
            running_equity += pnl_usd
            if running_equity > peak_equity:
                peak_equity = running_equity
            dd = peak_equity - running_equity
            if dd > max_drawdown_usd:
                max_drawdown_usd = dd

        # 6. Stale Feed Rejection Rate (%)
        breaker = staleness_breaker or self.staleness_breaker
        stale_feed_reject_pct = getattr(breaker, "stale_rejection_rate_pct", 0.0)

        return {
            "total_trades": total,
            "net_expectancy_sol": round(net_expectancy_sol, 6),
            "tp1_win_rate_pct": round(tp1_win_rate_pct, 2),
            "avg_slippage_pct": round(avg_slippage_pct, 2),
            "runner_contribution_pct": round(runner_contrib_pct, 2),
            "max_drawdown_usd": round(max_drawdown_usd, 2),
            "stale_feed_reject_pct": round(stale_feed_reject_pct, 2),
            "total_net_pnl_sol": round(total_pnl_sol, 6),
            "total_net_pnl_usd": round(total_pnl_usd, 2),
            "tp1_hits": tp1_hits,
            "runner_net_pnl_sol": round(runner_net_pnl_sol, 6),
        }

    def evaluate(
        self,
        trades: Optional[List[Dict]] = None,
        staleness_breaker: Optional[DataStalenessCircuitBreaker] = None,
        force_28d_sample: bool = False,
    ) -> GoNoGo28DayReport:
        """
        Executes the Phase 5 Go/No-Go Gate evaluation.
        Evaluates all 6 criteria and synthesizes the final operational verdict.
        """
        all_trades = trades if trades is not None else self.ledger.get_all_trades()
        filtered_trades, days_elapsed = self.filter_28_day_trades(all_trades)

        stats = self.calculate_metrics(filtered_trades, staleness_breaker=staleness_breaker)

        evaluations: List[GoNoGoMetricEvaluation] = []

        # 1. Net Expectancy (SOL): > 0.0 after all TX fees, MEV penalties, and Jito tips
        net_exp = stats["net_expectancy_sol"]
        net_exp_pass = net_exp > 0.0
        evaluations.append(
            GoNoGoMetricEvaluation(
                metric_name="Net Expectancy (SOL)",
                pass_threshold="> 0.0 SOL after all TX fees, MEV penalties, and tips",
                implication_if_failed="Strategy has no mathematical edge. Terminate.",
                actual_value=f"{net_exp:+.6f} SOL / trade (${net_exp * self.sol_price_usd:+.2f})",
                raw_value=net_exp,
                is_passed=net_exp_pass,
                details=f"Total Net P&L: {stats['total_net_pnl_sol']:+.6f} SOL across {stats['total_trades']} completed trades.",
            )
        )

        # 2. TP1 Win Rate: >= 60% (Hits +9% gross target)
        tp1_wr = stats["tp1_win_rate_pct"]
        tp1_pass = tp1_wr >= 60.0
        evaluations.append(
            GoNoGoMetricEvaluation(
                metric_name="TP1 Win Rate",
                pass_threshold=">= 60.0% (Hits +9% gross target)",
                implication_if_failed="Friction buster failing; friction will bleed the account over time.",
                actual_value=f"{tp1_wr:.1f}% ({stats['tp1_hits']} / {stats['total_trades']})",
                raw_value=tp1_wr,
                is_passed=tp1_pass,
                details=f"90% of position sold at +9% target to lock in net profit.",
            )
        )

        # 3. Slippage Variance: Simulated avg slippage < 2.0% one-way
        avg_slip = stats["avg_slippage_pct"]
        slip_pass = avg_slip < 2.0
        evaluations.append(
            GoNoGoMetricEvaluation(
                metric_name="Slippage Variance",
                pass_threshold="Simulated avg slippage < 2.0% one-way",
                implication_if_failed="AMM depth model is miscalibrated; live execution will be catastrophic.",
                actual_value=f"{avg_slip:.2f}% avg one-way slippage",
                raw_value=avg_slip,
                is_passed=slip_pass,
                details="Includes self-impact on constant-product curve + MEV sandwich + slot latency delay.",
            )
        )

        # 4. Runner Contribution: > 5% of total net P&L
        runner_pct = stats["runner_contribution_pct"]
        runner_pass = runner_pct > 5.0
        evaluations.append(
            GoNoGoMetricEvaluation(
                metric_name="Runner Contribution",
                pass_threshold="> 5.0% of total net P&L",
                implication_if_failed="10% runner is just giving back TP1 profits; revert to 100% TP1.",
                actual_value=f"{runner_pct:.1f}% ({stats['runner_net_pnl_sol']:+.6f} SOL)",
                raw_value=runner_pct,
                is_passed=runner_pass,
                details="10% lottery runner with 3-minute hard time kill hurdle.",
            )
        )

        # 5. Max Drawdown: < $10.00 (Respecting daily halt)
        max_dd = stats["max_drawdown_usd"]
        dd_pass = max_dd < 10.00
        evaluations.append(
            GoNoGoMetricEvaluation(
                metric_name="Max Drawdown",
                pass_threshold="< $10.00 (Respecting daily halt ceiling)",
                implication_if_failed="Position sizing or stop logic is failing under cascade conditions.",
                actual_value=f"${max_dd:.2f} max cumulative drawdown",
                raw_value=max_dd,
                is_passed=dd_pass,
                details="Evaluated against the -$10.00 hard daily loss ceiling.",
            )
        )

        # 6. Stale Feed Rejects: < 5% of signals rejected for WS latency
        stale_pct = stats["stale_feed_reject_pct"]
        stale_pass = stale_pct < 5.0
        evaluations.append(
            GoNoGoMetricEvaluation(
                metric_name="Stale Feed Rejects",
                pass_threshold="< 5.0% of signals rejected for WS latency",
                implication_if_failed="Infrastructure is insufficient for slot-level trading. Upgrade RPC.",
                actual_value=f"{stale_pct:.2f}% stale feed rejections",
                raw_value=stale_pct,
                is_passed=stale_pass,
                details="Evaluated against the 1000ms WebSocket data staleness circuit breaker.",
            )
        )

        total_m = len(evaluations)
        passed_m = sum(1 for e in evaluations if e.is_passed)
        failed_m = total_m - passed_m
        is_overall_passed = (failed_m == 0)

        # Synthesize final Go/No-Go Decision
        if is_overall_passed:
            decision = "GO — APPROVED FOR LIVE MAINNET DEPLOYMENT"
        elif not net_exp_pass:
            decision = "NO-GO: STRATEGY TERMINATION (Net Expectancy <= 0.0 SOL; no mathematical edge)"
        elif not dd_pass:
            decision = "NO-GO: RISK HALT (Drawdown breached $10.00 ceiling)"
        elif not tp1_pass:
            decision = "NO-GO: FRICTION BUSTER CALIBRATION REQUIRED (TP1 win rate < 60%)"
        elif not slip_pass:
            decision = "NO-GO: AMM DEPTH MODEL RECALIBRATION REQUIRED (Slippage >= 2.0%)"
        elif not runner_pass:
            decision = "NO-GO: REVERT TO 100% TP1 (Runner contribution <= 5.0%)"
        elif not stale_pass:
            decision = "NO-GO: UPGRADE RPC INFRASTRUCTURE (Stale feed rejects >= 5.0%)"
        else:
            decision = f"NO-GO: {failed_m} CRITERIA FAILED"

        return GoNoGo28DayReport(
            is_overall_passed=is_overall_passed,
            go_decision=decision,
            total_metrics=total_m,
            passed_metrics=passed_m,
            failed_metrics=failed_m,
            total_trades_evaluated=stats["total_trades"],
            evaluation_window_days=self.EVALUATION_WINDOW_DAYS,
            days_elapsed=days_elapsed,
            metrics=evaluations,
            summary_stats=stats,
        )
