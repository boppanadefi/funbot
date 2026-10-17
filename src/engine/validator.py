"""
60/40 In-Sample vs. Out-of-Sample Falsification Validator.
Evaluates simulated trade distribution against Phase 4 Acceptance Criteria.
"""

from typing import Dict, List, Tuple
from src.engine.models import ValidationReport


class OutOfSampleValidator:
    """
    Splits historical/paper trading records into a 60% calibration set and 40% chronologically
    contiguous out-of-sample set to verify mathematical edge stability.
    """

    MIN_OUT_OF_SAMPLE_TRADES = 10
    MAX_ALLOWABLE_DRAWDOWN_USD = 10.00  # Hard ceiling: max drawdown < $10.00
    MIN_WIN_RATE_REGIME_A = 70.0       # Target win-rate hurdle in Regime A
    MIN_WIN_RATE_REGIME_B = 65.0       # Target win-rate hurdle in Regime B

    def split_chronological_dataset(
        self,
        trades: List[Dict],
        in_sample_ratio: float = 0.60,
    ) -> Tuple[List[Dict], List[Dict]]:
        """
        Splits trades chronologically: oldest 60% in-sample, newest 40% out-of-sample.
        """
        sorted_trades = sorted(trades, key=lambda t: t.get("entry_ts", ""))
        split_idx = int(len(sorted_trades) * in_sample_ratio)
        in_sample = sorted_trades[:split_idx]
        out_of_sample = sorted_trades[split_idx:]
        return in_sample, out_of_sample

    def evaluate_dataset(
        self,
        trades: List[Dict],
        dataset_name: str = "OUT_OF_SAMPLE",
    ) -> ValidationReport:
        """
        Computes performance and validates all Phase 4 gate criteria.
        """
        rejection_reasons: List[str] = []
        if not trades:
            return ValidationReport(
                dataset_type=dataset_name,
                total_trades=0,
                winning_trades=0,
                losing_trades=0,
                win_rate_pct=0.0,
                gross_pnl_usd=0.0,
                total_friction_usd=0.0,
                net_pnl_usd=0.0,
                net_expectancy_per_trade_usd=0.0,
                profit_factor=0.0,
                max_drawdown_usd=0.0,
                is_gate_passed=False,
                rejection_reasons=["No trades in dataset"],
            )

        wins = [t for t in trades if t["net_pnl_usd"] > 0]
        losses = [t for t in trades if t["net_pnl_usd"] <= 0]
        total_count = len(trades)
        win_count = len(wins)
        loss_count = len(losses)
        win_rate = (win_count / total_count) * 100.0

        net_pnl = sum(t["net_pnl_usd"] for t in trades)
        expectancy = net_pnl / total_count
        gross_win = sum(t["net_pnl_usd"] for t in wins)
        gross_loss = abs(sum(t["net_pnl_usd"] for t in losses))
        profit_factor = (gross_win / gross_loss) if gross_loss > 0 else 99.0

        total_friction = sum(
            (t.get("venue_fees_sol", 0.0) + t.get("tips_sol", 0.0) + t.get("priority_fees_sol", 0.0)) * 155.0
            for t in trades
        )

        # Drawdown calculation
        running_cum = 0.0
        peak = 0.0
        max_dd = 0.0
        for t in trades:
            running_cum += t["net_pnl_usd"]
            if running_cum > peak:
                peak = running_cum
            dd = peak - running_cum
            if dd > max_dd:
                max_dd = dd

        # Gate Checks (Section 5.2)
        if expectancy <= 0:
            rejection_reasons.append(f"Net expectancy negative: ${expectancy:.2f} / trade")

        if max_dd >= self.MAX_ALLOWABLE_DRAWDOWN_USD:
            rejection_reasons.append(
                f"Max drawdown breached ceiling: ${max_dd:.2f} (Cap < ${self.MAX_ALLOWABLE_DRAWDOWN_USD:.2f})"
            )

        if win_rate < self.MIN_WIN_RATE_REGIME_B:
            rejection_reasons.append(
                f"Win rate below hurdle: {win_rate:.1f}% (Minimum >= {self.MIN_WIN_RATE_REGIME_B:.1f}%)"
            )

        is_passed = (len(rejection_reasons) == 0 and total_count >= 5)

        return ValidationReport(
            dataset_type=dataset_name,
            total_trades=total_count,
            winning_trades=win_count,
            losing_trades=loss_count,
            win_rate_pct=round(win_rate, 1),
            gross_pnl_usd=round(net_pnl + total_friction, 2),
            total_friction_usd=round(total_friction, 2),
            net_pnl_usd=round(net_pnl, 2),
            net_expectancy_per_trade_usd=round(expectancy, 3),
            profit_factor=round(profit_factor, 2),
            max_drawdown_usd=round(max_dd, 2),
            is_gate_passed=is_passed,
            rejection_reasons=rejection_reasons,
        )
