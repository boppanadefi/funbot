"""
Unit tests for Phase 5: Live Micro-Trading Control Center & Execution Bridge.
Verifies Guardrails 6 & 7:
- Emergency Kill Switch persistence and trip logic
- -$10.00 Daily Circuit Breaker and Pre-Flight Capacity Checks
- Atomic Jito MEV Bundle instruction ordering (tip strictly after swap)
- 1.5% notional tip cap and 0.005 SOL emergency exit reserve
- Execution Bridge operator sign-off and emergency liquidation
"""

import pytest
from pathlib import Path
import tempfile
import shutil

from src.live.circuit_breaker import DailyCircuitBreaker
from src.live.execution_bridge import ExecutionBridge
from src.live.jito_bundle_builder import (
    JitoBundleBuilder,
    TipCapExceededError,
    InvalidInstructionOrderError,
    InsufficientExitBudgetError,
    BundleInstruction,
)
from src.live.kill_switch import EmergencyKillSwitch, KillSwitchActiveError


@pytest.fixture
def temp_dir():
    d = tempfile.mkdtemp()
    yield Path(d)
    shutil.rmtree(d, ignore_errors=True)


def test_kill_switch_trip_and_reset(temp_dir):
    state_file = temp_dir / "kill_switch.json"
    ks = EmergencyKillSwitch(state_file_path=str(state_file))

    assert not ks.is_tripped()
    ks.assert_open()

    # Trip the switch
    ks.trip(reason="Testing panic trigger", operator="TEST_OP")
    assert ks.is_tripped()

    with pytest.raises(KillSwitchActiveError):
        ks.assert_open()

    # Verify persistence by instantiating new switch instance
    ks2 = EmergencyKillSwitch(state_file_path=str(state_file))
    assert ks2.is_tripped()

    # Reset with operator sign-off
    ks2.reset(operator="TEST_ADMIN", notes="Safety checks passed")
    assert not ks2.is_tripped()
    ks2.assert_open()


def test_circuit_breaker_loss_limit_and_capacity(temp_dir):
    state_file = temp_dir / "circuit_breaker.json"
    cb = DailyCircuitBreaker(state_file_path=str(state_file))

    assert cb.get_remaining_budget() == 10.00

    # Record small loss
    cb.record_trade_pnl(-3.50)
    assert cb.get_remaining_budget() == 6.50
    assert not cb.get_status()["is_halted"]

    # Pre-trade capacity check:
    # A $15 position with 25% stop + 10% gap = $5.25 + $3.00 reserve = $8.25 total risk.
    # $8.25 exceeds remaining budget of $6.50 -> Should REJECT
    ok, msg, exposure = cb.check_preflight_capacity(position_notional_usd=15.0)
    assert not ok
    assert "exceeds remaining daily budget" in msg

    # A smaller $5 position with 20% stop + 5% gap = $1.25 + $3.00 reserve = $4.25 risk -> Should PASS
    ok, msg, exposure = cb.check_preflight_capacity(
        position_notional_usd=5.0, stop_loss_pct=20.0, gap_penalty_pct=5.0
    )
    assert ok

    # Hit the -$10.00 ceiling
    cb.record_trade_pnl(-7.00)  # Total loss: -$10.50
    status = cb.get_status()
    assert status["is_halted"]
    assert cb.get_remaining_budget() == 0.0

    # Entries are completely halted
    ok, msg, _ = cb.check_preflight_capacity(position_notional_usd=5.0)
    assert not ok
    assert "Circuit Breaker" in msg

    # Reset
    cb.reset_circuit_breaker(operator="TEST_OP")
    assert not cb.get_status()["is_halted"]


def test_jito_bundle_builder_ordering_and_caps():
    builder = JitoBundleBuilder(sol_price_usd=155.0)
    pos_sol = 0.10  # ~0.10 SOL position ($15.50)

    # 1. Test 1.5% tip cap
    max_tip = builder.calculate_max_tip_sol(pos_sol)
    assert round(max_tip, 5) == 0.0015

    # Excessive tip should raise TipCapExceededError
    with pytest.raises(TipCapExceededError):
        builder.build_atomic_swap_bundle(
            pool_address="PoolTest11111111111111111111111111111111",
            token_mint="TokenTest11111111111111111111111111111111",
            is_buy=True,
            position_notional_sol=pos_sol,
            proposed_tip_sol=0.0020,  # 2.0% tip > 1.5% cap
            user_wallet_pubkey="WalletTest1111111111111111111111111111111",
        )

    # Valid tip (1.0% notional)
    bundle = builder.build_atomic_swap_bundle(
        pool_address="PoolTest11111111111111111111111111111111",
        token_mint="TokenTest11111111111111111111111111111111",
        is_buy=True,
        position_notional_sol=pos_sol,
        proposed_tip_sol=0.0010,
        user_wallet_pubkey="WalletTest1111111111111111111111111111111",
    )
    assert bundle.tip_sol == 0.0010
    assert bundle.emergency_exit_budget_sol == 0.005

    # Verify instruction ordering: swap must precede tip
    swap_ix = [ix for ix in bundle.instructions if "SWAP" in ix.instruction_type][0]
    tip_ix = [ix for ix in bundle.instructions if "TIP" in ix.instruction_type][0]
    assert swap_ix.order_index < tip_ix.order_index

    # 2. Test instruction ordering validator rejects inverted instructions
    bad_instructions = [
        BundleInstruction("System", "JITO_TIP_TRANSFER", [], "", order_index=0),
        BundleInstruction("PumpSwap", "PUMPSWAP_SWAP", [], "", order_index=1),
    ]
    with pytest.raises(InvalidInstructionOrderError):
        builder._verify_instruction_ordering(bad_instructions)

    # 3. Test emergency exit budget verification
    with pytest.raises(InsufficientExitBudgetError):
        builder.validate_exit_budget(wallet_free_sol=0.102, trade_sol=0.100)  # Leaves only 0.002 < 0.005 SOL

    assert builder.validate_exit_budget(wallet_free_sol=0.110, trade_sol=0.100)  # Leaves 0.010 > 0.005 SOL


def test_execution_bridge_workflow(temp_dir):
    ks = EmergencyKillSwitch(state_file_path=str(temp_dir / "ks.json"))
    cb = DailyCircuitBreaker(state_file_path=str(temp_dir / "cb.json"))
    builder = JitoBundleBuilder(sol_price_usd=155.0)
    bridge = ExecutionBridge(
        kill_switch=ks,
        circuit_breaker=cb,
        bundle_builder=builder,
        sol_price_usd=155.0,
        state_file=str(temp_dir / "orders.json"),
    )

    # Stage an order
    ok, msg, order = bridge.stage_order(
        order_id="ord_test_001",
        pool_address="PoolTest11111111111111111111111111111111",
        token_mint="TokenTest11111111111111111111111111111111",
        regime="REGIME_A",
        action="BUY",
        position_notional_usd=15.0,
        proposed_tip_sol=0.0010,
    )
    assert ok
    assert order.status == "PENDING_OPERATOR"

    # Operator approves and executes
    ok, msg, receipt = bridge.operator_approve_and_execute("ord_test_001", operator_id="TEST_ADMIN")
    assert ok
    assert receipt["status"] == "LANDED_CONFIRMED"

    # Stage another order, then reject
    bridge.stage_order(
        order_id="ord_test_002",
        pool_address="PoolTest22222222222222222222222222222222",
        token_mint="TokenTest22222222222222222222222222222222",
        regime="REGIME_B",
        action="BUY",
        position_notional_usd=15.0,
        proposed_tip_sol=0.0010,
    )
    assert bridge.operator_reject_order("ord_test_002", reason="Slippage too high")

    # Trip kill switch and verify staging is blocked
    ks.trip(reason="Market flash crash", operator="OPERATOR")
    ok, msg, _ = bridge.stage_order(
        order_id="ord_test_003",
        pool_address="PoolTest33333333333333333333333333333333",
        token_mint="TokenTest33333333333333333333333333333333",
        regime="REGIME_A",
        action="BUY",
        position_notional_usd=15.0,
        proposed_tip_sol=0.0010,
    )
    assert not ok
    assert "Kill Switch is ACTIVE" in msg

    # Emergency 100% liquidation dump
    dump = bridge.trigger_emergency_liquidation("PoolTest11111111111111111111111111111111", "TokenTest11111111111111111111111111111111")
    assert dump["action"] == "EMERGENCY_100_PCT_LIQUIDATION"
    assert dump["tip_sol"] == 0.005
