"""
Fresh Burner Wallet Detection Engine.
Enforces Guardrail 5: Flag buyers with < 3 lifetime pre-trade transactions.
"""

from typing import Dict, List, Set, Tuple
from src.cluster.models import WalletProfile


class BurnerWalletDetector:
    """
    Identifies temporary / burner wallets used by launch cartels and wash-trading bots.
    """

    def __init__(self, max_allowed_burner_ratio: float = 0.25):
        self.max_allowed_burner_ratio = max_allowed_burner_ratio
        # In-memory wallet profile cache
        self.profiles: Dict[str, WalletProfile] = {}

    def register_wallet(self, pubkey: str, pre_trade_tx_count: int, sol_bought: float = 0.0) -> WalletProfile:
        profile = WalletProfile(
            pubkey=pubkey,
            pre_trade_tx_count=pre_trade_tx_count,
            total_sol_bought=sol_bought,
        )
        self.profiles[pubkey] = profile
        return profile

    def evaluate_buyers(self, buyer_profiles: List[WalletProfile]) -> Tuple[int, float, List[str]]:
        """
        Evaluates a list of buyer profiles for a candidate pool.
        Returns:
            - burner_count: int
            - burner_volume_ratio: float [0.0 - 1.0]
            - burner_pubkeys: List[str]
        """
        if not buyer_profiles:
            return 0, 0.0, []

        burner_pubkeys = []
        burner_sol = 0.0
        total_sol = 0.0

        for p in buyer_profiles:
            total_sol += p.total_sol_bought
            if p.is_burner:
                burner_pubkeys.append(p.pubkey)
                burner_sol += p.total_sol_bought

        burner_count = len(burner_pubkeys)
        volume_ratio = (burner_sol / total_sol) if total_sol > 0 else (burner_count / len(buyer_profiles))

        return burner_count, round(volume_ratio, 4), burner_pubkeys

    def is_burner_volume_excessive(self, burner_volume_ratio: float) -> bool:
        """Returns True if fake/burner volume exceeds the acceptable threshold."""
        return burner_volume_ratio > self.max_allowed_burner_ratio
