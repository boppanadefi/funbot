"""
Unit Test Suite for Phase 7: Strategy & State Engine.
Covers:
1. Entry Decision Orchestrator (12 Checks) (todo-05)
2. Position Lifecycle State Machine (todo-04)
3. Exit Timer System (5 Concurrent Timers) (todo-06)
"""

import asyncio
import time
import pytest

from src.engine.entry_orchestrator import (
    CandidateContext,
    CheckStatus,
    EntryDecisionOrchestrator,
    EntryEvaluationResult,
    WSOL_MINT_STR,
    MIN_EXIT_RESERVE_SOL,
)
from src.engine.position_state_machine import (
    ExecutionMode,
    IllegalStateTransitionError,
    PositionContext,
    PositionState,
    PositionStateMachine,
)
from src.engine.exit_timer_manager import ExitTimerManager
from src.protocol.pumpswap import BondingCurveState, PUMPSWAP_PROGRAM_ID_STR
from src.security.authority_auditor import AuthorityAuditResult


# =====================================================================
# Fixtures & Helpers
# =====================================================================

def make_valid_context(
    pool_id: str = "Pool123456789012345678901234567890",
    elapsed_minutes: float = 12.5,
    vuw: float = 1.35,
    aggression: float = 2.45,
    demand: float = 180.0,
    mint_revoked: bool = True,
    freeze_revoked: bool = True,
    top10_conc: float = 14.5,
    wallet_sol: float = 2.50,
    remaining_budget_usd: float = 10.0,
    ws_age_ms: float = 120.0,
    is_regime_test: bool = False,
) -> CandidateContext:
    """Creates a 100% compliant context passing all 12 checks."""
    audit = AuthorityAuditResult(
        passed=mint_revoked and freeze_revoked and (top10_conc <= 25.0),
        reason="OK",
        mint_revoked=mint_revoked,
        freeze_revoked=freeze_revoked,
        top10_concentration_pct=top10_conc,
        is_metadata_immutable=True,
        mint_address="MintDezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263",
    )
    curve = BondingCurveState(
        virtual_token_reserves=1_000_000_000_000_000,
        virtual_sol_reserves=32_000_000_000,
        real_token_reserves=793_000_000_000_000,
        real_sol_reserves=2_000_000_000,
        token_total_supply=1_000_000_000_000_000,
        complete=False,
    )
    return CandidateContext(
        pool_id=pool_id,
        quote_mint=WSOL_MINT_STR,
        program_id=PUMPSWAP_PROGRAM_ID_STR,
        elapsed_minutes=elapsed_minutes,
        vuw=vuw,
        aggression_ratio=aggression,
        net_buy_volume_usd_60s=demand,
        authority_audit=audit,
        bonding_curve=curve,
        sol_price_usd=150.0,
        wallet_balance_sol=wallet_sol,
        remaining_daily_budget_usd=remaining_budget_usd,
        circuit_breaker_halted=False,
        last_ws_message_age_ms=ws_age_ms,
        is_regime_test=is_regime_test,
        requested_size_usd=25.0,
    )


# =====================================================================
# 1. The 12-Check Entry Gate Tests (todo-05)
# =====================================================================

class TestEntryDecisionOrchestrator:
    def test_all_12_checks_pass(self):
        """Verify compliant pool passes all 12 checks with suggested position size."""
        ctx = make_valid_context()
        result = EntryDecisionOrchestrator.evaluate(ctx)

        assert result.passed is True
        assert result.passed_count == 12
        assert result.failed_count == 0
        assert len(result.rejection_reasons) == 0
        assert result.suggested_size_sol > 0.10  # ~$25 / $150 = ~0.1667 SOL

    def test_check1_age_window_failures(self):
        """Check 1: Fails if elapsed time < 8.0m (cooling) or > 25.0m (expired)."""
        # Premature (< 8.0m)
        ctx_young = make_valid_context(elapsed_minutes=5.2)
        res_young = EntryDecisionOrchestrator.evaluate(ctx_young)
        assert res_young.passed is False
        assert "Check 1 Failed" in res_young.rejection_reasons[0]
        assert res_young.checks["01_age_window"].status == CheckStatus.FAIL

        # Expired (> 25.0m)
        ctx_old = make_valid_context(elapsed_minutes=26.8)
        res_old = EntryDecisionOrchestrator.evaluate(ctx_old)
        assert res_old.passed is False
        assert "Check 1 Failed" in res_old.rejection_reasons[0]

    def test_check2_canonical_pair_failure(self):
        """Check 2: Fails if quote is not WSOL or program is non-canonical."""
        ctx = make_valid_context()
        ctx.quote_mint = "USDC111111111111111111111111111111111111111"
        res = EntryDecisionOrchestrator.evaluate(ctx)
        assert res.passed is False
        assert any("Check 2 Failed" in r for r in res.rejection_reasons)
        assert res.checks["02_canonical_pair"].status == CheckStatus.FAIL

    def test_check3_vuw_failure_and_regime_test_override(self):
        """Check 3: Fails if VUW < 1.20 in normal regime, but passes at 1.05 in REGIME_TEST."""
        # Normal regime: VUW 1.10 fails (requires 1.20)
        ctx_norm = make_valid_context(vuw=1.10, is_regime_test=False)
        res_norm = EntryDecisionOrchestrator.evaluate(ctx_norm)
        assert res_norm.passed is False
        assert any("Check 3 Failed" in r for r in res_norm.rejection_reasons)

        # REGIME_TEST: VUW 1.10 passes (relaxed to 1.05)
        ctx_test = make_valid_context(vuw=1.10, is_regime_test=True)
        res_test = EntryDecisionOrchestrator.evaluate(ctx_test)
        assert res_test.checks["03_velocity_unique_wallets"].status == CheckStatus.PASS

    def test_check4_aggression_ratio_failure(self):
        """Check 4: Fails if aggression ratio <= 2.00."""
        ctx = make_valid_context(aggression=1.85)
        res = EntryDecisionOrchestrator.evaluate(ctx)
        assert res.passed is False
        assert any("Check 4 Failed" in r for r in res.rejection_reasons)
        assert res.checks["04_aggression_ratio"].status == CheckStatus.FAIL

    def test_check5_demand_floor_failure(self):
        """Check 5: Fails if 60s demand volume < $100/min."""
        ctx = make_valid_context(demand=65.0, is_regime_test=False)
        res = EntryDecisionOrchestrator.evaluate(ctx)
        assert res.passed is False
        assert any("Check 5 Failed" in r for r in res.rejection_reasons)

    def test_check6_check7_authorities_failure(self):
        """Checks 6 & 7: Fails if mint or freeze authorities remain active."""
        ctx_freeze = make_valid_context(freeze_revoked=False)
        res_freeze = EntryDecisionOrchestrator.evaluate(ctx_freeze)
        assert res_freeze.passed is False
        assert any("Check 6 Failed" in r for r in res_freeze.rejection_reasons)

        ctx_mint = make_valid_context(mint_revoked=False)
        res_mint = EntryDecisionOrchestrator.evaluate(ctx_mint)
        assert res_mint.passed is False
        assert any("Check 7 Failed" in r for r in res_mint.rejection_reasons)

    def test_check8_top10_concentration_failure(self):
        """Check 8: Fails if top 10 concentration > 25.0%."""
        ctx = make_valid_context(top10_conc=31.5)
        res = EntryDecisionOrchestrator.evaluate(ctx)
        assert res.passed is False
        assert any("Check 8 Failed" in r for r in res.rejection_reasons)

    def test_check10_risk_capacity_failure(self):
        """Check 10: Fails if risk exceeds remaining daily budget or breaker halted."""
        # Remaining budget only $2.00 (risk demand = $25 * 0.15 + $0.50 + reserve = ~$5)
        ctx = make_valid_context(remaining_budget_usd=2.0)
        res = EntryDecisionOrchestrator.evaluate(ctx)
        assert res.passed is False
        assert any("Check 10 Failed" in r for r in res.rejection_reasons)

    def test_check11_emergency_exit_reserve_failure(self):
        """Check 11: Fails if balance after trade drops below 0.005 SOL."""
        # Wallet has 0.17 SOL; trade needs ~0.1667 SOL -> remainder < 0.005 SOL
        ctx = make_valid_context(wallet_sol=0.17)
        res = EntryDecisionOrchestrator.evaluate(ctx)
        assert res.passed is False
        assert any("Check 11 Failed" in r for r in res.rejection_reasons)

    def test_check12_ws_latency_failure(self):
        """Check 12: Fails if WS feed is stale (> 1000 ms)."""
        ctx = make_valid_context(ws_age_ms=1450.0)
        res = EntryDecisionOrchestrator.evaluate(ctx)
        assert res.passed is False
        assert any("Check 12 Failed" in r for r in res.rejection_reasons)


# =====================================================================
# 2. Position Lifecycle State Machine Tests (todo-04)
# =====================================================================

class TestPositionStateMachine:
    def setup_method(self):
        self.context = PositionContext(
            pool_id="PoolA",
            mint="MintA",
            mode=ExecutionMode.PATH_A_PLUS,
            entry_price_sol=0.00005,
            size_tokens=1_000_000,
            size_sol_notional=0.1667,
            entry_timestamp=time.time(),
            profit_hurdle_sol=0.00005 * 1.16,  # +16% hurdle
            stop_price_sol=0.00005 * 0.90,     # -10% initial stop
        )

    def test_valid_path_a_plus_lifecycle(self):
        """Verify normal Path A+ lifecycle: IDLE -> PENDING_ENTRY -> ACTIVE_OPEN -> TRAILED_STOP_LOCKED -> PENDING_EXIT -> CLOSED."""
        sm = PositionStateMachine(self.context)
        assert sm.current_state == PositionState.PENDING_ENTRY

        # 1. Entry fills
        sm.transition_to(PositionState.ACTIVE_OPEN, trigger="TX_CONFIRMED", price_sol=0.00005)
        assert sm.current_state == PositionState.ACTIVE_OPEN

        # 2. Price climbs and crosses hurdle (+16%) -> Ratchets stop
        sm.update_price_tick(0.000060)
        assert sm.current_state == PositionState.TRAILED_STOP_LOCKED
        assert sm.context.stop_price_sol == 0.00005 * 1.08  # Locked in +8%

        # 3. Stop triggered
        sm.transition_to(PositionState.PENDING_EXIT, trigger="TRAILING_STOP_HIT", price_sol=0.000055)
        assert sm.current_state == PositionState.PENDING_EXIT

        # 4. Liquidation confirms
        record = sm.transition_to(PositionState.CLOSED, trigger="EXIT_TX_CONFIRMED", price_sol=0.000055)
        assert sm.current_state == PositionState.CLOSED
        assert sm.is_terminal is True
        assert sm.context.realized_pnl_pct > 0.0

    def test_invariant2_path_a_plus_forbids_tranche_states(self):
        """Verify Invariant 2: attempting tranche states in Path A+ raises IllegalStateTransitionError."""
        sm = PositionStateMachine(self.context)
        sm.transition_to(PositionState.ACTIVE_OPEN, trigger="TX_CONFIRMED", price_sol=0.00005)

        with pytest.raises(IllegalStateTransitionError, match="forbidden under Invariant 2 Path A+"):
            sm.transition_to(PositionState.TP1_SCALING, trigger="ATTEMPT_TRANCHE")

    def test_illegal_state_skip(self):
        """Verify skipping required intermediate states raises IllegalStateTransitionError."""
        sm = PositionStateMachine(self.context)
        with pytest.raises(IllegalStateTransitionError, match="Cannot transition from PENDING_ENTRY to CLOSED"):
            sm.transition_to(PositionState.CLOSED, trigger="ILLEGAL_SKIP")

    def test_entry_cancellation_flow(self):
        """Verify unconfirmed entry cleanly transitions to CANCELLED."""
        sm = PositionStateMachine(self.context)
        sm.transition_to(PositionState.CANCELLED, trigger="TX_TIMEOUT_OR_DROPPED")
        assert sm.current_state == PositionState.CANCELLED
        assert sm.is_terminal is True


# =====================================================================
# 3. Exit Timer System Tests (todo-06)
# =====================================================================

class TestExitTimerManager:
    @pytest.mark.asyncio
    async def test_lifespan_timer_expiration(self):
        """Verify lifespan timer fires timeout callback upon expiry."""
        fired_events = []

        async def callback(timer_name: str, pos_id: str):
            fired_events.append((timer_name, pos_id))

        tm = ExitTimerManager(
            position_id="Pos123",
            on_timeout_callback=callback,
            max_lifespan_sec=0.05,  # 50ms for test
        )

        tm.start_lifespan_timer()
        assert tm.is_timer_running("lifespan_20m")

        await asyncio.sleep(0.08)
        assert len(fired_events) == 1
        assert fired_events[0] == ("lifespan_20m", "Pos123")

    @pytest.mark.asyncio
    async def test_cancel_timer_prevents_firing(self):
        """Verify cancelling a running timer aborts callback execution."""
        fired_events = []

        async def callback(timer_name: str, pos_id: str):
            fired_events.append((timer_name, pos_id))

        tm = ExitTimerManager(
            position_id="Pos123",
            on_timeout_callback=callback,
            runner_kill_sec=0.05,
        )

        tm.start_runner_kill_timer()
        assert tm.is_timer_running("runner_kill_3m")

        # Cancel immediately
        tm.cancel_timer("runner_kill_3m")
        assert not tm.is_timer_running("runner_kill_3m")

        await asyncio.sleep(0.08)
        assert len(fired_events) == 0

    @pytest.mark.asyncio
    async def test_cancel_all_cleans_up_all_tasks(self):
        """Verify cancel_all terminates multiple active timers cleanly."""
        tm = ExitTimerManager(position_id="PosMulti")
        tm.start_lifespan_timer(0.2)
        tm.start_runner_kill_timer(0.2)
        tm.start_tx_confirmation_watchdog(0.2)

        assert len(tm.active_timer_names) == 3
        tm.cancel_all()
        assert len(tm.active_timer_names) == 0
