"""
Unit Tests for Phase 3: Dynamic Cost Engine & Pre-Flight Double Gate.
Validates Guardrail 4 (Dual-Hurdle Double Gate) and Guardrail 6 (Jito Tip Caps).
"""

import pytest
from src.cost.fee_tiers import FeeTierLookup, RegimeType
from src.cost.jito_tips import JitoTipModel
from src.cost.slippage_model import SlippageModel
from src.cost.failure_penalties import FailurePenaltyModel, BundleOutcome
from src.cost.double_gate import PreFlightDoubleGate
from src.cluster.models import ClusterRiskScore, SafetyCheckResult
from datetime import datetime, timezone


def test_fee_tier_lookup():
    lookup = FeeTierLookup()

    # Tier 1: 350 SOL MC -> 1.250% (Regime A)
    t1 = lookup.get_tier(350.0, is_canonical=True)
    assert t1.tier == 1
    assert t1.total_fee_pct == 1.250
    assert t1.regime == RegimeType.REGIME_A
    assert t1.is_enabled is True

    # Tier 2: 800 SOL MC -> 1.200% (Regime B)
    t2 = lookup.get_tier(800.0, is_canonical=True)
    assert t2.tier == 2
    assert t2.total_fee_pct == 1.200
    assert t2.regime == RegimeType.REGIME_B

    # Tier 3: 1800 SOL MC -> 1.150% (Regime B)
    t3 = lookup.get_tier(1800.0, is_canonical=True)
    assert t3.tier == 3
    assert t3.total_fee_pct == 1.150

    # Non-canonical pair -> Disabled
    non_canon = lookup.get_tier(500.0, is_canonical=False)
    assert non_canon.is_enabled is False
    assert non_canon.regime == RegimeType.REGIME_C


def test_jito_tip_caps_and_emergency_budget():
    tip_model = JitoTipModel(sol_price_usd=155.0)

    # $15 position -> 0.09677 SOL. Cap at 1.5% notional = ~0.00145 SOL.
    # Normal p50 tip = 0.0010 SOL <= cap -> capped is True
    res_normal = tip_model.calculate_entry_overhead(15.0, p50_tip_sol=0.0010)
    assert res_normal.is_entry_tip_capped is True
    assert res_normal.jito_tip_sol == 0.0010

    # Excessive tip during congestion: 0.0030 SOL > 0.00145 SOL -> capped is False, tip capped
    res_congested = tip_model.calculate_entry_overhead(15.0, p50_tip_sol=0.0030)
    assert res_congested.is_entry_tip_capped is False
    assert res_congested.jito_tip_sol < 0.0015

    # Exit leg has emergency 0.005 SOL budget
    res_exit_emergency = tip_model.calculate_exit_overhead(15.0, is_emergency=True)
    assert res_exit_emergency.jito_tip_sol == 0.005


def test_slippage_model():
    model = SlippageModel(sol_price_usd=155.0)

    # $15 position on 85 SOL pool -> impact = (0.09677 / 170) * 100 ≈ 0.057%
    res = model.estimate_impact(15.0, 85.0)
    assert res.passes_depth_screen is True
    assert res.entry_impact_pct < 0.10
    # Stressed impact should include +1.0% adverse slippage buffer
    assert res.stressed_roundtrip_impact_pct > res.expected_roundtrip_impact_pct

    # Shallow liquidity pool: $15 on 1.0 SOL pool -> impact > 2.0% -> rejected
    shallow = model.estimate_impact(15.0, 1.0)
    assert shallow.passes_depth_screen is False


def test_failure_penalties_post_swap_tip_spared():
    model = FailurePenaltyModel(sol_price_usd=155.0)

    # Landed revert: Tip placed post-swap in atomic bundle is NOT charged!
    res_revert = model.evaluate_bundle_outcome(
        outcome=BundleOutcome.LANDED_REVERT,
        tip_sol=0.0015,
        priority_fee_sol=0.0001,
        position_notional_usd=15.0,
    )
    assert res_revert.tip_charged is False
    assert res_revert.total_penalty_sol == (0.000005 + 0.0001)

    # Expired bundle: 0 fees charged
    res_expired = model.evaluate_bundle_outcome(
        outcome=BundleOutcome.NOT_LANDED_EXPIRED,
        tip_sol=0.0015,
        priority_fee_sol=0.0001,
        position_notional_usd=15.0,
    )
    assert res_expired.total_penalty_sol == 0.0


def test_preflight_double_gate():
    gate = PreFlightDoubleGate(sol_price_usd=155.0)

    # Healthy candidate in Regime A (MC = 390 SOL, Reserve = 85 SOL)
    res = gate.evaluate(
        pool_address="PoolHealthy",
        token_mint="TokenHealthy",
        market_cap_sol=390.0,
        current_sol_reserve=85.0,
        position_notional_usd=15.0,
        is_canonical=True,
    )
    assert res.is_passed is True
    assert res.regime == RegimeType.REGIME_A
    assert res.expected_cost_pct <= 7.00
    assert res.stress_cost_pct <= 10.00
    # Ratchet trigger typically between +16% and +22%
    assert 16.0 <= res.ratchet_trigger_gross_pct <= 22.0

    # Candidate with shallow reserve failing depth impact
    res_shallow = gate.evaluate(
        pool_address="PoolShallow",
        token_mint="TokenShallow",
        market_cap_sol=390.0,
        current_sol_reserve=1.5,
        position_notional_usd=15.0,
    )
    assert res_shallow.is_passed is False
    assert any("Depth inadequate" in r for r in res_shallow.rejection_reasons)

    # Candidate failing Phase 2 cluster safety
    bad_cluster = ClusterRiskScore(
        pool_address="PoolClusterTrap",
        token_mint="TokenClusterTrap",
        evaluated_ts=datetime.now(timezone.utc),
        total_buyers=10,
        burner_count=8,
        burner_volume_ratio=0.75,
        top_parent_pubkey="EvilParent",
        top_parent_concentration=0.60,
        sybil_detected=True,
        safety=SafetyCheckResult(
            token_mint="TokenClusterTrap",
            mint_authority_revoked=True,
            freeze_authority_revoked=False,  # Honeypot
            is_canonical_sol=True,
            is_safe=False,
            failure_reasons=["Freeze authority active"],
        ),
        composite_risk_score=0.95,
        is_approved=False,
        rejection_reasons=["Burner volume excessive"],
    )
    res_cluster_fail = gate.evaluate(
        pool_address="PoolClusterTrap",
        token_mint="TokenClusterTrap",
        market_cap_sol=390.0,
        current_sol_reserve=85.0,
        cluster_score=bad_cluster,
    )
    assert res_cluster_fail.is_passed is False
    assert any("Phase 2 Cluster Risk Rejected" in r for r in res_cluster_fail.rejection_reasons)
