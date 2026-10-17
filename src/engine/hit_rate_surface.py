"""
Required Win-Rate Surface (p*) & Expectancy Engine.
Encodes the required win-rate formula from bot.md (v2.5) Section 8: p* = |L| / (W + |L|).
"""

from dataclasses import dataclass
from typing import Dict, List, Tuple


@dataclass
class SurfacePoint:
    avg_win_pct: float
    avg_loss_pct: float
    required_win_rate_pct: float
    net_expectancy_at_70pct: float


class HitRateSurfaceAnalyzer:
    """
    Computes required win rates and generates expectancy grid matrices.
    """

    @staticmethod
    def calculate_required_win_rate(avg_win_usd: float, avg_loss_usd: float) -> float:
        """
        Calculates break-even win rate p* = |L| / (W + |L|).
        """
        abs_loss = abs(avg_loss_usd)
        if avg_win_usd + abs_loss == 0:
            return 100.0
        p_star = abs_loss / (avg_win_usd + abs_loss)
        return round(p_star * 100.0, 2)

    @classmethod
    def generate_surface_grid(
        cls,
        win_range: Tuple[float, float, float] = (10.0, 40.0, 5.0),
        loss_range: Tuple[float, float, float] = (5.0, 15.0, 2.5),
        position_size_usd: float = 15.0,
    ) -> List[SurfacePoint]:
        """
        Generates a 2D matrix of required win rates across varying profit targets and stop losses.
        """
        points = []
        w_start, w_end, w_step = win_range
        l_start, l_end, l_step = loss_range

        w = w_start
        while w <= w_end:
            l = l_start
            while l <= l_end:
                win_usd = position_size_usd * (w / 100.0)
                loss_usd = position_size_usd * (l / 100.0)
                p_star = cls.calculate_required_win_rate(win_usd, loss_usd)

                # Expectancy at default target win rate (70%)
                expectancy = (0.70 * win_usd) - (0.30 * loss_usd)

                points.append(
                    SurfacePoint(
                        avg_win_pct=round(w, 1),
                        avg_loss_pct=round(l, 1),
                        required_win_rate_pct=p_star,
                        net_expectancy_at_70pct=round(expectancy, 3),
                    )
                )
                l += l_step
            w += w_step

        return points
