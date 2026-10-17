"""
Consolidated Cluster Risk Scoring Engine.
Synthesizes burner detection, 2-hop funding graph, Sybil patterns, and token authorities
into a normalized composite risk score and candidate pass/fail decision.
"""

from datetime import datetime, timezone
from typing import Dict, List, Optional
from src.cluster.models import (
    ClusterRiskScore,
    SafetyCheckResult,
    WalletProfile,
)
from src.cluster.burner_detection import BurnerWalletDetector
from src.cluster.funding_graph import FundingGraphAnalyzer
from src.cluster.sybil_heuristics import SybilDetector
from src.cluster.safety_checks import TokenSafetyAuditor


class ClusterRiskEngine:
    """
    Main entry point for Phase 2 cluster and risk analysis.
    """

    def __init__(
        self,
        burner_detector: Optional[BurnerWalletDetector] = None,
        graph_analyzer: Optional[FundingGraphAnalyzer] = None,
        sybil_detector: Optional[SybilDetector] = None,
        safety_auditor: Optional[TokenSafetyAuditor] = None,
    ):
        self.burner_detector = burner_detector or BurnerWalletDetector()
        self.graph_analyzer = graph_analyzer or FundingGraphAnalyzer()
        self.sybil_detector = sybil_detector or SybilDetector()
        self.safety_auditor = safety_auditor or TokenSafetyAuditor()

    def evaluate_candidate(
        self,
        pool_address: str,
        token_mint: str,
        buyer_profiles: List[WalletProfile],
        swaps: List[Dict],
        mint_authority: Optional[str] = None,
        freeze_authority: Optional[str] = None,
        quote_mint: str = "So11111111111111111111111111111111111111112",
    ) -> ClusterRiskScore:
        """
        Executes full cluster and risk evaluation against a candidate pool.
        """
        now = datetime.now(timezone.utc)
        rejection_reasons: List[str] = []

        # 1. Authority Safety Audit (Guardrail 7)
        safety = self.safety_auditor.audit_token(
            token_mint=token_mint,
            mint_authority=mint_authority,
            freeze_authority=freeze_authority,
            quote_mint=quote_mint,
        )
        if not safety.is_safe:
            rejection_reasons.extend(safety.failure_reasons)

        # 2. Burner Wallet Detection (Guardrail 5)
        burner_count, burner_ratio, burner_pubkeys = self.burner_detector.evaluate_buyers(buyer_profiles)
        if self.burner_detector.is_burner_volume_excessive(burner_ratio):
            rejection_reasons.append(
                f"Burner volume excessive: {burner_ratio*100.0:.1f}% "
                f"(Cap <= {self.burner_detector.max_allowed_burner_ratio*100.0:.1f}%)"
            )

        # 3. 2-Hop Funding Graph Concentration (Guardrail 5)
        buyer_pubkeys = [p.pubkey for p in buyer_profiles]
        top_parent, top_concentration, _ = self.graph_analyzer.analyze_buyer_cluster(buyer_pubkeys)
        if self.graph_analyzer.is_concentration_excessive(top_concentration):
            rejection_reasons.append(
                f"Common parent funding concentration: {top_concentration*100.0:.1f}% "
                f"from {top_parent} (Cutoff <= {self.graph_analyzer.max_parent_concentration*100.0:.1f}%)"
            )

        # 4. Sybil Swarm Heuristics
        sybil_detected, sybil_groups = self.sybil_detector.detect_coordinated_swarms(swaps)
        if sybil_detected:
            rejection_reasons.append(f"Coordinated Sybil swarm detected ({len(sybil_groups)} cluster groups)")

        # 5. Composite Risk Score Calculation [0.0 - 1.0]
        if not safety.is_safe:
            composite_score = 1.0
        else:
            base_score = (burner_ratio * 0.40) + (top_concentration * 0.40)
            if sybil_detected:
                base_score += 0.20
            composite_score = min(1.0, max(0.0, round(base_score, 4)))

        # Determine approval
        is_approved = (len(rejection_reasons) == 0 and composite_score <= 0.40)

        return ClusterRiskScore(
            pool_address=pool_address,
            token_mint=token_mint,
            evaluated_ts=now,
            total_buyers=len(buyer_profiles),
            burner_count=burner_count,
            burner_volume_ratio=burner_ratio,
            top_parent_pubkey=top_parent,
            top_parent_concentration=top_concentration,
            sybil_detected=sybil_detected,
            safety=safety,
            composite_risk_score=composite_score,
            is_approved=is_approved,
            rejection_reasons=rejection_reasons,
        )
