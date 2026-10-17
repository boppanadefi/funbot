"""
Unit tests for Fix 5: Dynamic Liquidity-Based Sizing.
Tests:
1. Maximum Safe Swap SOL calculation at 1.5% price impact.
2. Dynamic sizing rule: Position Size = min(Calculated Max Swap, $25 Active Wallet Limit).
3. Hard rejection when Calculated Max Swap < $3.00 (Solana fixed tx overhead floor).
4. Auto-resizing for thin liquidity pools (e.g. 1.3 SOL -> ~$6.05 entry).
5. Wallet limit capping at $25.00 for deep liquidity pools.
6. PreFlightDoubleGate integration with dynamic sizing.
"""

import pytest
from src.cost.slippage_model import SlippageModel
from src.cost.double_gate import PreFlightDoubleGate, DoubleGateResult
from src.cost.fee_tiers import RegimeType


def test_calculate_max_safe_swap_sol_and_usd():
    model = SlippageModel(sol_price_usd=155.0)

    # Constant-product formula:
    # entry_impact_pct = (swap_size_sol / (2.0 * current_sol_reserve)) * 100.0
    # swap_size_sol = (1.5 / 100.0) * (2.0 * current_sol_reserve) = 0.030 * current_sol_reserve

    # 1. Thin pool: 1.3 SOL reserve -> swap = 0.030 * 1.3 = 0.039 SOL -> $6.045 (~$6.05)
    swap_sol_1_3 = model.calculate_max_safe_swap_sol(1.3, max_acceptable_impact_pct=1.50)
    assert swap_sol_1_3 == pytest.approx(0.039, rel=1e-3)
    swap_usd_1_3 = model.calculate_max_safe_swap_usd(1.3, max_acceptable_impact_pct=1.50)
    assert 6.00 <= swap_usd_1_3 <= 6.10

    # 2. Medium pool: 10 SOL reserve -> swap = 0.30 SOL -> $46.50
    swap_sol_10 = model.calculate_max_safe_swap_sol(10.0, max_acceptable_impact_pct=1.50)
    assert swap_sol_10 == pytest.approx(0.30, rel=1e-3)
    swap_usd_10 = model.calculate_max_safe_swap_usd(10.0, max_acceptable_impact_pct=1.50)
    assert swap_usd_10 == pytest.approx(46.50, rel=1e-2)

    # 3. Deep pool: 30 SOL reserve -> swap = 0.90 SOL -> $139.50
    swap_sol_30 = model.calculate_max_safe_swap_sol(30.0, max_acceptable_impact_pct=1.50)
    assert swap_sol_30 == pytest.approx(0.90, rel=1e-3)
    swap_usd_30 = model.calculate_max_safe_swap_usd(30.0, max_acceptable_impact_pct=1.50)
    assert swap_usd_30 == pytest.approx(139.50, rel=1e-2)

    # 4. Zero or negative reserve handling
    assert model.calculate_max_safe_swap_sol(0.0) == 0.0
    assert model.calculate_max_safe_swap_sol(-5.0) == 0.0
    assert model.calculate_max_safe_swap_usd(0.0) == 0.0


def test_dynamic_sizing_rule_and_hard_floor_rejection():
    model = SlippageModel(sol_price_usd=155.0)

    # Scenario A: Ultra-shallow pool where Max Safe Swap < $3.00 floor -> HARD REJECT
    # Reserve = 0.5 SOL -> Max swap = 0.03 * 0.5 * 155 = $2.325 < $3.00
    size, is_viable, reason = model.calculate_dynamic_position_size_usd(0.5)
    assert is_viable is False
    assert size == 0.0
    assert "minimum viable threshold" in reason
    assert "< $3.00" in reason

    # Scenario B: Thin pool with ~1.3 SOL reserve -> auto-resizes to ~$6.05
    size_thin, is_viable_thin, reason_thin = model.calculate_dynamic_position_size_usd(1.3)
    assert is_viable_thin is True
    assert 6.00 <= size_thin <= 6.10
    assert "Dynamically sized" in reason_thin

    # Scenario C: Pool with 3.0 SOL reserve -> max safe swap = 0.03 * 3.0 * 155 = $13.95
    # Does not reach $25 cap, so sized to $13.95
    size_med, is_viable_med, _ = model.calculate_dynamic_position_size_usd(3.0)
    assert is_viable_med is True
    assert size_med == pytest.approx(13.95, abs=0.05)

    # Scenario D: Deep pool with 30 SOL reserve -> max safe swap = $139.50
    # Capped at $25.00 Active Wallet Limit
    size_deep, is_viable_deep, _ = model.calculate_dynamic_position_size_usd(30.0)
    assert is_viable_deep is True
    assert size_deep == 25.00


def test_double_gate_dynamic_sizing_integration():
    gate = PreFlightDoubleGate(sol_price_usd=155.0)

    # 1. Candidate with 30 SOL reserve:
    # Under old static $20 model, if depth check evaluated $20, it would be fine,
    # but under dynamic sizing, it caps at $25 wallet limit and has very low impact (0.27%)
    res_deep = gate.evaluate(
        pool_address="Pool30SOL",
        token_mint="Token30SOL",
        market_cap_sol=390.0,
        current_sol_reserve=30.0,
        position_notional_usd=25.0,
        use_dynamic_sizing=True,
    )
    assert res_deep.is_passed is True
    assert res_deep.dynamic_position_size_usd == 25.00
    assert res_deep.expected_impact_pct < 1.0

    # 2. Candidate with 0.5 SOL reserve:
    # Max safe swap $2.33 < $3.00 -> Hard reject
    res_ultra_shallow = gate.evaluate(
        pool_address="PoolUltraShallow",
        token_mint="TokenUltraShallow",
        market_cap_sol=390.0,
        current_sol_reserve=0.5,
        use_dynamic_sizing=True,
    )
    assert res_ultra_shallow.is_passed is False
    assert any("Depth inadequate" in r for r in res_ultra_shallow.rejection_reasons)
    assert any("< $3.00" in r for r in res_ultra_shallow.rejection_reasons)

    # 3. Candidate with 1.5 SOL reserve:
    # Auto-resizes to ~$6.97. Does NOT fail depth screen!
    res_thin = gate.evaluate(
        pool_address="PoolThin",
        token_mint="TokenThin",
        market_cap_sol=390.0,
        current_sol_reserve=1.5,
        use_dynamic_sizing=True,
    )
    assert not any("Depth inadequate" in r for r in res_thin.rejection_reasons)
    assert 6.95 <= res_thin.dynamic_position_size_usd <= 7.00
