"""
End-to-End Cross-Phase Integration Test for Milestone 2 (v3.0).
Validates seamless dataflow across:
- Phase 6 (Protocol & AMM Engine)
- Phase 7 (Strategy & State Engine)
- Phase 8 (Execution & MEV Pipeline)
- Phase 9 (Orchestrator & Production Reliability)
"""

import asyncio
import os
import tempfile
import time
import pytest

from src.protocol.pumpswap import (
    PumpSwapProgram,
    BondingCurveState,
    PUMPSWAP_PROGRAM_ID_STR,
    FEE_RECIPIENT_STR,
    SYSTEM_PROGRAM_STR,
    TOKEN_PROGRAM_STR,
    ASSOCIATED_TOKEN_PROGRAM_STR,
    RENT_SYSVAR_STR,
)
from src.amm.quote_calculator import AMMQuoteCalculator
from src.security.authority_auditor import AuthorityAuditResult
from src.engine.entry_orchestrator import (
    CandidateContext,
    EntryDecisionOrchestrator,
    WSOL_MINT_STR,
)
from src.engine.position_state_machine import (
    PositionStateMachine,
    PositionContext,
    PositionState,
    ExecutionMode,
)
from src.engine.exit_timer_manager import ExitTimerManager
from src.engine.event_bus import EventBus, LiveSwapEvent
from src.engine.staleness_breaker import DataStalenessCircuitBreaker
from src.live.wire_tx_builder import (
    SolanaWireTransactionBuilder,
    build_set_compute_unit_limit,
    build_set_compute_unit_price,
    build_system_transfer,
)
from src.live.jito_client import JitoClient, JITO_TIP_ACCOUNTS
from src.live.confirmation_monitor import ConfirmationMonitor, ConfirmationStatus
from src.live.circuit_breaker import DailyCircuitBreaker
from src.live.kill_switch import EmergencyKillSwitch
from src.live.wallet_manager import WalletAccount, b58encode, b58decode
from src.orchestrator.scheduler import AsyncScheduler, TaskPriority
from src.orchestrator.persistence import StatePersistenceManager
from src.orchestrator.alerting import AlertDispatcher, AlertSeverity
from src.orchestrator.main_loop import MasterTradingOrchestrator


class TestMilestone2E2EIntegration:
    @pytest.mark.asyncio
    async def test_full_milestone2_pipeline_lifecycle(self):
        """
        Executes a complete trade lifecycle verifying Invariants 1-7 across all 4 phases:
        1. Phase 6: Binary instructions and bonding curve quote calculations
        2. Phase 7: 12-Check qualification and position state transitions
        3. Phase 8: Wire transaction building and Jito tip compliance
        4. Phase 9: Master orchestrator coordination, SQLite WAL persistence, and alerting
        """
        temp_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        db_path = os.path.join(temp_dir.name, "e2e_orchestrator.db")
        cb_path = os.path.join(temp_dir.name, "e2e_cb.json")
        ks_path = os.path.join(temp_dir.name, "e2e_ks.json")

        try:
            # 1. Setup Wallet & Protocol Accounts (Phase 6 & 8)
            wallet = WalletAccount(
                pubkey="71kM3Wc7hS4nFzYt8Q2n5K1p9M6a4v8e3r1t7y9u5i2o",
                is_live=False,
                label="E2E Active Wallet",
                secret_seed=b"\x09" * 32,
            )
            mint_pubkey = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"
            program = PumpSwapProgram(PUMPSWAP_PROGRAM_ID_STR)

            # Derive PDAs (Phase 6)
            bonding_curve_pda, _ = program.get_bonding_curve_pda(mint_pubkey)
            associated_bc_ata = program.get_bonding_curve_ata(
                bonding_curve_pda, mint_pubkey
            )
            assert bonding_curve_pda is not None
            assert associated_bc_ata is not None

            # 2. Mathematical AMM Quote (Phase 6)
            bonding_curve_state = BondingCurveState(
                virtual_token_reserves=1_000_000_000_000_000,
                virtual_sol_reserves=32_000_000_000,
                real_token_reserves=793_000_000_000_000,
                real_sol_reserves=2_000_000_000,
                token_total_supply=1_000_000_000_000_000,
                complete=False,
            )
            buy_quote = AMMQuoteCalculator.get_buy_quote(
                virtual_sol=bonding_curve_state.virtual_sol_reserves,
                virtual_token=bonding_curve_state.virtual_token_reserves,
                sol_in_lamports=160_000_000,  # ~0.16 SOL ($25 size)
                slippage_bps=100,  # 1.0%
                real_token_reserves=bonding_curve_state.real_token_reserves,
            )
            assert buy_quote.expected_tokens_out > 0
            assert buy_quote.fee_lamports == 1_600_000

            # 3. Security Authority Audit (Phase 6)
            authority_audit = AuthorityAuditResult(
                passed=True,
                reason="Revocations and concentration verified",
                mint_revoked=True,
                freeze_revoked=True,
                top10_concentration_pct=14.2,
                is_metadata_immutable=True,
                mint_address=mint_pubkey,
            )

            # 4. Strategy & State Engine (Phase 7) — 12-Check Gate
            candidate = CandidateContext(
                pool_id="PoolE2E111111111111111111111111111111111",
                quote_mint=WSOL_MINT_STR,
                program_id=PUMPSWAP_PROGRAM_ID_STR,
                elapsed_minutes=15.0,  # Within 8-25m window
                vuw=1.45,             # >= 1.20
                aggression_ratio=2.85, # > 2.00
                net_buy_volume_usd_60s=220.0, # >= $100/min
                authority_audit=authority_audit,
                bonding_curve=bonding_curve_state,
                sol_price_usd=155.0,
                wallet_balance_sol=2.50,
                remaining_daily_budget_usd=10.0,
                circuit_breaker_halted=False,
                last_ws_message_age_ms=85.0, # <= 1000ms
                is_regime_test=False,
                requested_size_usd=25.0,
            )

            eval_result = EntryDecisionOrchestrator.evaluate(candidate)
            assert eval_result.passed
            assert eval_result.failed_count == 0

            # 5. Wire Transaction Construction & Jito Compliant Tip (Phase 8)
            buy_ix = program.build_buy_instruction(
                user_address=wallet.pubkey,
                mint_address=mint_pubkey,
                amount_tokens=buy_quote.expected_tokens_out,
                max_sol_cost_lamports=buy_quote.max_sol_cost_lamports,
            )

            jito_client = JitoClient()
            tip_account = jito_client.get_random_tip_account()
            # 1.5% notional tip cap enforcement (Invariant 6)
            tip_lamports = jito_client.validate_tip_budget(
                tip_lamports=100_000,
                notional_lamports=160_000_000,
                wallet_balance_lamports=2_500_000_000,
            )
            tip_ix = build_system_transfer(
                sender=wallet.pubkey,
                recipient=tip_account,
                lamports=tip_lamports,
            )

            # Invariant 6: Tip strictly after swap instruction
            instructions = [
                build_set_compute_unit_limit(250_000),
                build_set_compute_unit_price(10_000),
                buy_ix,
                tip_ix,
            ]
            assert instructions[-1].program_id == SYSTEM_PROGRAM_STR  # Tip is strictly last

            wire_bytes, wire_b58, sig_b58 = SolanaWireTransactionBuilder.build_signed_wire_transaction(
                wallet=wallet,
                instructions=instructions,
                recent_blockhash="5t1C1mN2a2mC7Z9W6d7T8H5h9L4p3Q2n1M8v6K4r2E1w",
            )
            assert len(wire_bytes) > 200
            assert len(sig_b58) > 80

            # 6. Master Orchestrator, SQLite WAL Persistence & Alerting (Phase 9)
            persistence = StatePersistenceManager(db_path=db_path)
            circuit_breaker = DailyCircuitBreaker(state_file_path=cb_path)
            kill_switch = EmergencyKillSwitch(state_file_path=ks_path)
            staleness_breaker = DataStalenessCircuitBreaker(max_staleness_ms=1000.0)
            event_bus = EventBus()
            alert_dispatcher = AlertDispatcher()
            scheduler = AsyncScheduler(num_workers=2)

            orchestrator = MasterTradingOrchestrator(
                scheduler=scheduler,
                persistence=persistence,
                alert_dispatcher=alert_dispatcher,
                event_bus=event_bus,
                staleness_breaker=staleness_breaker,
                circuit_breaker=circuit_breaker,
                kill_switch=kill_switch,
                is_regime_test=False,
                sol_price_usd=155.0,
            )

            await orchestrator.start()
            staleness_breaker.record_message_received(slot=458325000)

            # Execute entry via Orchestrator
            entry_res = await orchestrator.evaluate_and_enter(candidate)
            assert entry_res.passed
            assert orchestrator.active_position_count == 1

            # Check SQLite WAL persistence
            persisted_positions = persistence.load_active_positions()
            assert len(persisted_positions) == 1
            assert persisted_positions[0]["pool_id"] == candidate.pool_id

            # Verify Exit Timers are running
            fsm, timer_mgr = orchestrator.active_positions[candidate.pool_id]
            assert "lifespan_20m" in timer_mgr.active_timer_names
            assert "tx_watchdog_30s" in timer_mgr.active_timer_names

            # 7. Price Appreciation & Trailing Profit Ratchet (+15% hurdle crossing)
            hurdle_price = fsm.context.profit_hurdle_sol
            pump_swap = LiveSwapEvent(
                pool_address=candidate.pool_id,
                token_mint=mint_pubkey,
                is_buy=True,
                sol_amount=hurdle_price * 1.05,
                token_amount=1.0,
                trader_pubkey="TraderWhale111111111111111111111111111",
                slot=458325050,
            )
            await orchestrator._process_active_position_swap(pump_swap)

            # State should have ratcheted to TRAILED_STOP_LOCKED (+8% lock)
            assert fsm.current_state == PositionState.TRAILED_STOP_LOCKED
            assert fsm.context.stop_price_sol >= fsm.context.entry_price_sol * 1.08

            # 8. Stop Hit & Clean Liquidation
            breach_price = fsm.context.stop_price_sol * 0.98
            dump_swap = LiveSwapEvent(
                pool_address=candidate.pool_id,
                token_mint=mint_pubkey,
                is_buy=False,
                sol_amount=breach_price,
                token_amount=1.0,
                trader_pubkey="TraderDump111111111111111111111111111",
                slot=458325100,
            )
            await orchestrator._process_active_position_swap(dump_swap)

            # Position should be closed with locked profit
            assert orchestrator.active_position_count == 0
            assert fsm.current_state == PositionState.CLOSED
            assert fsm.context.realized_pnl_pct > 0.0

            # Verify SQLite closed record
            assert len(persistence.load_active_positions()) == 0

            # Verify Circuit Breaker recorded the positive trade PnL
            cb_status = circuit_breaker.get_status()
            assert cb_status["trades_count"] == 1
            assert cb_status["net_daily_pnl_usd"] > 0.0
            assert not cb_status["is_halted"]

            # Graceful shutdown
            await orchestrator.shutdown()
            assert not orchestrator.is_running
            assert not scheduler.is_running

        finally:
            try:
                temp_dir.cleanup()
            except Exception:
                pass
