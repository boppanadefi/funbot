"""
2-Hop Shared Funding Graph Engine.
Enforces Guardrail 5: Trace funding 2 hops back and block candidates if >30% originate from a common parent.
Includes CEX hot wallet and liquidity router whitelisting to eliminate false positives.
"""

from collections import defaultdict
from typing import Dict, List, Optional, Set, Tuple
from src.cluster.models import FundingHop


# Known CEX hot wallets, bridges, and infrastructure routers that distribute funds legitimately
KNOWN_CEX_AND_ROUTER_WHITELIST: Set[str] = {
    # Binance hot wallets
    "5tzFkiKscFZsp5hyA5m3w6q8HnC9m2k8A4iZ",
    "2ojv9BAiHUrvsm9gxDe7fJSzbNZVXcxctp3f",
    # Coinbase hot wallets
    "H8sMJSCQxfKiFTCfDR3DUMLPwcRbM61LGFJ8",
    "2AQdpHJ2JpcEgPiATUXjQxA8QmafFegfLivat",
    # Bybit / Kraken hot wallets
    "AC5RDfQFmDS1deWZos921qbhirSmWNxj5KAC",
    "FWznbcNXWQuHTawe9RxvQ2LdCENssh12dsZf",
    # Jupiter / Raydium / System Program
    "JUP4Fb2cqiRUcaTHdrPC8h2gNsA2ETXiPDD33WcGuJB",
    "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8",
    "11111111111111111111111111111111",
}


class FundingGraphAnalyzer:
    """
    Constructs and analyzes the directed funding graph up to 2 hops back from active token buyers.
    """

    def __init__(
        self,
        max_parent_concentration: float = 0.30,
        whitelist: Optional[Set[str]] = None,
    ):
        self.max_parent_concentration = max_parent_concentration
        self.whitelist = whitelist or KNOWN_CEX_AND_ROUTER_WHITELIST
        # child_pubkey -> List of FundingHop
        self.funding_edges: Dict[str, List[FundingHop]] = defaultdict(list)

    def add_funding_hop(
        self,
        child_pubkey: str,
        parent_pubkey: str,
        amount_sol: float,
        hop_level: int = 1,
        tx_signature: Optional[str] = None,
    ) -> None:
        """Register a funding transaction between a parent and child wallet."""
        hop = FundingHop(
            child_pubkey=child_pubkey,
            parent_pubkey=parent_pubkey,
            amount_sol=amount_sol,
            hop_level=hop_level,
            tx_signature=tx_signature,
        )
        self.funding_edges[child_pubkey].append(hop)

    def trace_root_ancestor(self, wallet_pubkey: str) -> Tuple[str, int]:
        """
        Traces a wallet back up to 2 hops to find its root funder.
        Returns:
            (root_funder_pubkey, hops_traversed)
        """
        current_wallet = wallet_pubkey
        hops = 0

        for hop_level in (1, 2):
            parents = self.funding_edges.get(current_wallet, [])
            if not parents:
                break
            # Pick primary funder (highest SOL funding amount)
            primary_parent = max(parents, key=lambda h: h.amount_sol)
            current_wallet = primary_parent.parent_pubkey
            hops += 1

            # Stop if reached a known CEX / liquidity router
            if current_wallet in self.whitelist:
                return (f"CEX_WHITELISTED:{current_wallet[:8]}", hops)

        return (current_wallet, hops)

    def analyze_buyer_cluster(self, buyer_pubkeys: List[str]) -> Tuple[Optional[str], float, Dict[str, int]]:
        """
        Analyzes a candidate token's buyer cluster for common funding origins.
        Returns:
            - top_parent_pubkey: Optional[str]
            - top_concentration_ratio: float [0.0 - 1.0]
            - root_funder_counts: Dict[str, int]
        """
        if not buyer_pubkeys:
            return None, 0.0, {}

        root_funder_counts: Dict[str, int] = defaultdict(int)
        valid_buyers_analyzed = 0

        for buyer in buyer_pubkeys:
            root_funder, hops = self.trace_root_ancestor(buyer)
            # Whitelisted CEX routers are aggregated benignly and do not count as a single colluding cartel
            if root_funder.startswith("CEX_WHITELISTED"):
                continue

            root_funder_counts[root_funder] += 1
            valid_buyers_analyzed += 1

        if not root_funder_counts or valid_buyers_analyzed == 0:
            return None, 0.0, dict(root_funder_counts)

        # Find the dominant parent address
        top_parent, max_count = max(root_funder_counts.items(), key=lambda item: item[1])
        top_concentration = max_count / len(buyer_pubkeys)

        return top_parent, round(top_concentration, 4), dict(root_funder_counts)

    def is_concentration_excessive(self, top_concentration: float) -> bool:
        """Guardrail 5 check: Returns True if common parent funding exceeds 30% cutoff."""
        return top_concentration > self.max_parent_concentration
