"""
Unit Tests for Phase 2: Cluster Analyzer & Risk Engine.
Validates Guardrail 5 (Burner detection & 2-hop funding graph) and Guardrail 7 (Safety checks).
"""

import pytest
from src.cluster.models import WalletProfile
from src.cluster.burner_detection import BurnerWalletDetector
from src.cluster.funding_graph import FundingGraphAnalyzer, KNOWN_CEX_AND_ROUTER_WHITELIST
from src.cluster.sybil_heuristics import SybilDetector
from src.cluster.safety_checks import TokenSafetyAuditor
from src.cluster.risk_scoring import ClusterRiskEngine
from src.telemetry.pool_discovery import NATIVE_SOL_MINT


def test_burner_wallet_detection():
    detector = BurnerWalletDetector(max_allowed_burner_ratio=0.25)
    
    # Wallet 1: 0 prior txs (Fresh burner)
    w1 = detector.register_wallet("BuyerFresh1", pre_trade_tx_count=0, sol_bought=1.5)
    assert w1.is_burner is True

    # Wallet 2: 2 prior txs (Fresh burner)
    w2 = detector.register_wallet("BuyerFresh2", pre_trade_tx_count=2, sol_bought=1.0)
    assert w2.is_burner is True

    # Wallet 3: 15 prior txs (Established wallet)
    w3 = detector.register_wallet("BuyerEstablished", pre_trade_tx_count=15, sol_bought=7.5)
    assert w3.is_burner is False

    # Total SOL = 10.0, Burner SOL = 2.5 (25.0%)
    burner_count, ratio, pubkeys = detector.evaluate_buyers([w1, w2, w3])
    assert burner_count == 2
    assert ratio == 0.25
    assert "BuyerFresh1" in pubkeys
    assert detector.is_burner_volume_excessive(ratio) is False

    # Add another burner pushing ratio over 25%
    w4 = detector.register_wallet("BuyerFresh3", pre_trade_tx_count=1, sol_bought=2.0)
    _, excessive_ratio, _ = detector.evaluate_buyers([w1, w2, w3, w4])
    assert detector.is_burner_volume_excessive(excessive_ratio) is True


def test_funding_graph_2_hop_traversal_and_cutoff():
    analyzer = FundingGraphAnalyzer(max_parent_concentration=0.30)

    # 4 buyers funded by the same parent wallet (RootFunderEvil) via 2 hops:
    # Evil -> ChildIntermediary -> Buyer1, Buyer2, Buyer3, Buyer4
    analyzer.add_funding_hop("ChildIntermediary", "RootFunderEvil", amount_sol=20.0, hop_level=1)
    analyzer.add_funding_hop("Buyer1", "ChildIntermediary", amount_sol=5.0, hop_level=2)
    analyzer.add_funding_hop("Buyer2", "ChildIntermediary", amount_sol=5.0, hop_level=2)
    analyzer.add_funding_hop("Buyer3", "ChildIntermediary", amount_sol=5.0, hop_level=2)
    analyzer.add_funding_hop("Buyer4", "ChildIntermediary", amount_sol=5.0, hop_level=2)

    # 4 independent buyers funded directly by Binance CEX
    binance_wallet = list(KNOWN_CEX_AND_ROUTER_WHITELIST)[0]
    for i in range(5, 9):
        analyzer.add_funding_hop(f"Buyer{i}", binance_wallet, amount_sol=2.0, hop_level=1)

    all_buyers = [f"Buyer{i}" for i in range(1, 9)]
    top_parent, concentration, roots = analyzer.analyze_buyer_cluster(all_buyers)

    # RootFunderEvil accounts for 4 / 8 buyers = 50.0% > 30% cutoff
    assert top_parent == "RootFunderEvil"
    assert concentration == 0.50
    assert analyzer.is_concentration_excessive(concentration) is True


def test_sybil_swarm_heuristics():
    detector = SybilDetector(min_cluster_size=3, max_size_std_dev=0.05)

    # Coordinated buy swarm: 3 wallets buying in slot 300_000_100 with identical 1.50 SOL size
    coordinated_swaps = [
        {"trader_pubkey": "Bot1", "sol_amount": 1.50, "slot": 300_000_100, "is_buy": True},
        {"trader_pubkey": "Bot2", "sol_amount": 1.51, "slot": 300_000_100, "is_buy": True},
        {"trader_pubkey": "Bot3", "sol_amount": 1.49, "slot": 300_000_100, "is_buy": True},
    ]

    detected, groups = detector.detect_coordinated_swarms(coordinated_swaps)
    assert detected is True
    assert len(groups) == 1
    assert groups[0].coordinated_tx_count == 3
    assert groups[0].mean_sol_size == 1.50

    # Uncoordinated swaps with large variance
    uncoordinated_swaps = [
        {"trader_pubkey": "TraderA", "sol_amount": 0.20, "slot": 300_000_200, "is_buy": True},
        {"trader_pubkey": "TraderB", "sol_amount": 4.50, "slot": 300_000_200, "is_buy": True},
        {"trader_pubkey": "TraderC", "sol_amount": 1.10, "slot": 300_000_200, "is_buy": True},
    ]
    detected_uncoord, _ = detector.detect_coordinated_swarms(uncoordinated_swaps)
    assert detected_uncoord is False


def test_token_safety_auditor():
    auditor = TokenSafetyAuditor()

    # 1. Perfectly safe token (revoked mint, revoked freeze, canonical SOL)
    safe_res = auditor.audit_token(
        token_mint="TokenSafe111111111111111111111111111111111",
        mint_authority=None,
        freeze_authority=None,
        quote_mint=NATIVE_SOL_MINT,
    )
    assert safe_res.is_safe is True
    assert len(safe_res.failure_reasons) == 0

    # 2. Honeypot with active freeze authority (Guardrail 7 rejection)
    honeypot_res = auditor.audit_token(
        token_mint="TokenTrapFreeze",
        mint_authority=None,
        freeze_authority="MaliciousFreezeAuthorityPubkey",
        quote_mint=NATIVE_SOL_MINT,
    )
    assert honeypot_res.is_safe is False
    assert any("Freeze authority active" in r for r in honeypot_res.failure_reasons)

    # 3. Non-canonical quote pair (e.g. fake USDC pair)
    fake_quote_res = auditor.audit_token(
        token_mint="TokenFakeQuote",
        mint_authority=None,
        freeze_authority=None,
        quote_mint="EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
    )
    assert fake_quote_res.is_safe is False
    assert any("Non-canonical quote pair" in r for r in fake_quote_res.failure_reasons)


def test_cluster_risk_engine_end_to_end():
    engine = ClusterRiskEngine()

    # Create safe buyer profiles
    safe_buyers = [
        WalletProfile(pubkey=f"SafeBuyer{i}", pre_trade_tx_count=20, total_sol_bought=1.0)
        for i in range(10)
    ]
    swaps = [
        {"trader_pubkey": f"SafeBuyer{i}", "sol_amount": float(i + 1), "slot": 300_000_000 + i * 10, "is_buy": True}
        for i in range(10)
    ]

    score = engine.evaluate_candidate(
        pool_address="PoolSafe100",
        token_mint="TokenMintSafe",
        buyer_profiles=safe_buyers,
        swaps=swaps,
        mint_authority=None,
        freeze_authority=None,
        quote_mint=NATIVE_SOL_MINT,
    )

    assert score.is_approved is True
    assert score.composite_risk_score <= 0.40
    assert len(score.rejection_reasons) == 0

    # Candidate with active freeze authority should fail immediately with score 1.0
    bad_score = engine.evaluate_candidate(
        pool_address="PoolTrap",
        token_mint="TokenMintTrap",
        buyer_profiles=safe_buyers,
        swaps=swaps,
        mint_authority=None,
        freeze_authority="DevKey",
        quote_mint=NATIVE_SOL_MINT,
    )
    assert bad_score.is_approved is False
    assert bad_score.composite_risk_score == 1.0
