"""
Sybil Pattern Detection Heuristics.
Detects coordinated launch patterns (same slot/second timing, identical SOL sizes).
"""

from collections import defaultdict
import statistics
from typing import Dict, List, Optional, Tuple
from src.cluster.models import SybilGroup


class SybilDetector:
    """
    Detects Sybil rings and bot swarms that coordinate purchases across multiple wallets.
    """

    def __init__(
        self,
        min_cluster_size: int = 3,
        max_size_std_dev: float = 0.05,
    ):
        self.min_cluster_size = min_cluster_size
        self.max_size_std_dev = max_size_std_dev

    def detect_coordinated_swarms(
        self,
        swaps: List[Dict],
    ) -> Tuple[bool, List[SybilGroup]]:
        """
        Scans a series of swap events for coordinated Sybil signatures.
        Swaps dict format: {"trader_pubkey": str, "sol_amount": float, "slot": int, "is_buy": bool}
        """
        buys = [s for s in swaps if s.get("is_buy", True)]
        if len(buys) < self.min_cluster_size:
            return False, []

        # Group buys by slot
        slot_groups: Dict[int, List[Dict]] = defaultdict(list)
        for buy in buys:
            slot_groups[buy["slot"]].append(buy)

        flagged_groups: List[SybilGroup] = []

        for slot, slot_buys in slot_groups.items():
            if len(slot_buys) >= self.min_cluster_size:
                sol_sizes = [b["sol_amount"] for b in slot_buys]
                mean_size = statistics.mean(sol_sizes)
                stdev = statistics.stdev(sol_sizes) if len(sol_sizes) > 1 else 0.0

                # If standard deviation of buy sizes in the same slot is extremely tight,
                # it strongly indicates automated batch buys from a single bot controller
                if stdev <= self.max_size_std_dev:
                    wallets = list({b["trader_pubkey"] for b in slot_buys})
                    group = SybilGroup(
                        group_id=f"sybil_slot_{slot}",
                        wallets=wallets,
                        slot=slot,
                        mean_sol_size=round(mean_size, 3),
                        size_variance=round(stdev, 4),
                        coordinated_tx_count=len(slot_buys),
                    )
                    flagged_groups.append(group)

        return len(flagged_groups) > 0, flagged_groups
