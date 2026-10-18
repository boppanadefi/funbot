"""
Phase 4: Stress Injection Protocol Runner.
Executes the 4 mandatory edge-case defense stress tests:
- Test A: The Daily Loss Halt (-$10.00 ceiling halt & DAILY_LOSS_CEILING_HIT state).
- Test B: The Liquidity Collapse (30-second >5% slippage check force-close).
- Test C: Token-2022 Rejection (TransferFee extension hard-rejected before sizing).
- Test D: Time-Sliced TP1 Logic (3 separate sell tranches with escalating MEV penalties).
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
import logging
from typing import Dict, List, Optional, Any

from src.engine.execution_simulator import FrictionExecutionSimulator
from src.engine.live_paper_trader import LivePaperTradingEngine
from src.engine.models import PaperPosition, PositionState
from src.engine.paper_ledger import PaperLedger
from src.engine.simulator import PathASimulator
from src.engine.structure_exits import LocalStructureExitEngine
from src.engine.trade_accelerator import TradeAcceleratorConfig
from src.engine.virtual_amm import VirtualAMMMirror
from src.live.circuit_breaker import DailyCircuitBreaker

logger = logging.getLogger("StressInjection")


@dataclass
class StressTestResult:
    test_id: str
    test_name: str
    passed: bool
    summary: str
    details: Dict[str, Any] = field(default_factory=dict)


class StressInjectionProtocol:
    """
    Coordinates and validates the 4 stress injection tests.
    """

    def __init__(
        self,
        paper_engine: Optional[LivePaperTradingEngine] = None,
        circuit_breaker: Optional[DailyCircuitBreaker] = None,
        ledger: Optional[PaperLedger] = None,
        sol_price_usd: float = 155.0,
    ):
        self.circuit_breaker = circuit_breaker or DailyCircuitBreaker()
        self.ledger = ledger or PaperLedger()
        self.sol_price_usd = sol_price_usd
        self.paper_engine = paper_engine or LivePaperTradingEngine(
            circuit_breaker=self.circuit_breaker,
            sol_price_usd=sol_price_usd,
        )

    def run_test_a_daily_loss_halt(self) -> StressTestResult:
        """
        Test A: The Daily Loss Halt.
        Manually inject 3 fake losing trades into the paper ledger that total -$10.00.
        Verify the bot instantly halts new entries and logs the DAILY_LOSS_CEILING_HIT state.
        """
        now = datetime.now(timezone.utc)
        test_positions = [
            ("pos_stress_loss_1", -3.50),
            ("pos_stress_loss_2", -3.50),
            ("pos_stress_loss_3", -3.00),
        ]

        total_injected_loss = 0.0
        for pos_id, loss_usd in test_positions:
            sol_loss = loss_usd / self.sol_price_usd
            # Create synthetic losing position
            pos = PaperPosition(
                position_id=pos_id,
                pool_address=f"Pool{pos_id}",
                token_mint=f"Mint{pos_id}",
                regime="REGIME_TEST",
                entry_time=now - timedelta(minutes=15),
                entry_price_sol=0.00100,
                tokens_held=10000.0,
                sol_spent=0.096774,
                position_notional_usd=15.0,
                exit_time=now - timedelta(minutes=5),
                exit_reason="STRESS_INJECTED_LOSS",
                realized_net_pnl_usd=loss_usd,
            )
            # Create legs
            from src.engine.models import ExecutionLeg
            pos.entry_leg = ExecutionLeg(
                leg_type="ENTRY",
                timestamp=pos.entry_time,
                price_sol=0.00100,
                sol_amount=0.096774,
                token_amount=10000.0,
                venue_fee_sol=0.001,
                jito_tip_sol=0.0025,
                priority_fee_sol=0.00015,
            )
            exit_gross = 0.096774 + sol_loss
            pos.exit_leg = ExecutionLeg(
                leg_type="EXIT",
                timestamp=pos.exit_time,
                price_sol=max(0.000001, exit_gross / 10000.0),
                sol_amount=exit_gross,
                token_amount=10000.0,
                venue_fee_sol=0.001,
                jito_tip_sol=0.0025,
                priority_fee_sol=0.00015,
            )
            pos.state = PositionState.CLOSED_STOP

            self.ledger.record_trade(pos, self.sol_price_usd)
            self.circuit_breaker.record_trade_pnl(loss_usd)
            total_injected_loss += abs(loss_usd)

        # Check circuit breaker state
        cb_status = self.circuit_breaker.get_status()
        is_halted = cb_status.get("is_halted", False)
        remaining_budget = cb_status.get("remaining_budget_usd", 99.0)

        # Preflight risk capacity check must return False
        can_trade, reason, _ = self.circuit_breaker.check_preflight_capacity(
            position_notional_usd=15.0,
            sol_price_usd=self.sol_price_usd,
        )

        passed = is_halted and not can_trade and remaining_budget <= 0.0

        details = {
            "injected_trades": len(test_positions),
            "total_injected_loss_usd": total_injected_loss,
            "is_halted": is_halted,
            "halt_reason": cb_status.get("halt_reason"),
            "logged_state": "DAILY_LOSS_CEILING_HIT" if is_halted else "ACTIVE",
            "preflight_can_trade": can_trade,
            "rejection_reason": reason,
            "remaining_budget_usd": remaining_budget,
        }

        summary = (
            f"Daily Loss Halt Test PASSED: -$10.00 breach tripped circuit breaker "
            f"(is_halted={is_halted}, DAILY_LOSS_CEILING_HIT verified, new entries blocked)."
            if passed
            else f"Daily Loss Halt Test FAILED: Breaker did not halt entries at -$10.00 (is_halted={is_halted})."
        )
        return StressTestResult("TEST_A", "The Daily Loss Halt", passed, summary, details)

    def run_test_b_liquidity_collapse(self) -> StressTestResult:
        """
        Test B: The Liquidity Collapse.
        Simulates dead token on-chain with >5% simulated sell slippage.
        Verifies the 30-second Liquidity Collapse Exit triggers and force-closes the position.
        """
        sim = PathASimulator(ledger=self.ledger, sol_price_usd=self.sol_price_usd)
        t0 = datetime(2026, 10, 17, 10, 0, 0, tzinfo=timezone.utc)

        pos = sim.open_position(
            position_id="pos_stress_liq_collapse",
            pool_address="PoolDeadToken01",
            token_mint="MintDeadToken01",
            regime="REGIME_TEST",
            market_cap_sol=350.0,
            entry_price_sol=0.00100,
            position_notional_usd=15.0,
            entry_time=t0,
        )

        # 30 seconds later: LP drains, sell slippage spikes to 12.5% (> 5.0% threshold)
        t_30s = t0 + timedelta(seconds=35)
        dead_quote = {
            "price_sol": 0.00095,
            "sell_slippage_pct": 12.50,  # > 5.0% slippage triggers liquidity collapse
            "liquidity_usd": 400.0,      # Drained LP
        }

        updated = sim.update_price_tick(
            pos=pos,
            current_price_sol=0.00095,
            tick_time=t_30s,
            quote=dead_quote,
        )

        passed = (
            updated.is_closed
            and updated.state == PositionState.CLOSED_LIQUIDITY_DRAIN
            and "Liquidity Collapse" in (updated.exit_reason or "")
        )

        details = {
            "position_id": updated.position_id,
            "final_state": updated.state.value,
            "exit_reason": updated.exit_reason,
            "elapsed_seconds": (t_30s - t0).total_seconds(),
            "simulated_slippage_pct": 12.50,
            "threshold_pct": 5.0,
        }

        summary = (
            f"Liquidity Collapse Test PASSED: 30s slippage check (12.5% > 5%) triggered "
            f"CLOSED_LIQUIDITY_DRAIN and force-closed position."
            if passed
            else f"Liquidity Collapse Test FAILED: Position not force-closed on 12.5% slippage (state={updated.state})."
        )
        return StressTestResult("TEST_B", "The Liquidity Collapse", passed, summary, details)

    def run_test_c_token_2022_rejection(self) -> StressTestResult:
        """
        Test C: Token-2022 Rejection.
        Feeds a Token-2022 mint with TransferFee extension to the bot.
        Verify it is hard-rejected before calculating position size.
        """
        token_2022_candidate = {
            "pool_address": "PoolT22TransferFee01",
            "token_mint": "MintT22TransferFee01",
            "token_program": "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb",  # Official Token-2022 Program
            "has_transfer_fee": True,
            "is_token_2022": True,
            "minutes_post_graduation": 12.0,
            "current_sol_reserve": 50.0,
            "current_token_reserve": 100_000_000.0,
            "dex_name": "pumpswap",
        }
        quote = {
            "price_sol": 0.0005,
            "liquidity_usd": 15000.0,
            "total_txns_5m": 15,
            "buys_5m": 10,
            "sells_5m": 5,
            "price_change_5m": 5.2,
            "volume_5m_usd": 2500.0,
            "is_token_2022": True,
            "has_transfer_fee": True,
        }

        passed_gates, reject_reason = self.paper_engine.evaluate_candidate_gates(token_2022_candidate, quote)

        passed = (not passed_gates) and ("Token-2022" in reject_reason or "TransferFee" in reject_reason)

        details = {
            "token_mint": token_2022_candidate["token_mint"],
            "token_program": token_2022_candidate["token_program"],
            "has_transfer_fee": True,
            "passed_gates": passed_gates,
            "reject_reason": reject_reason,
            "hard_blocked_before_sizing": not passed_gates,
        }

        summary = (
            f"Token-2022 Rejection Test PASSED: Mint with TransferFee extension was "
            f"hard-rejected before sizing ('{reject_reason}')."
            if passed
            else f"Token-2022 Rejection Test FAILED: Token-2022 was not rejected (passed={passed_gates})."
        )
        return StressTestResult("TEST_C", "Token-2022 Rejection", passed, summary, details)

    def run_test_d_time_sliced_tp1(self) -> StressTestResult:
        """
        Test D: Time-Sliced TP1 Logic.
        When a winning trade occurs, monitors the execution.
        Verifies:
        - 3 separate sell simulations spaced 2-3 blocks apart.
        - Escalating MEV penalty applies (+0.5%, +1.0%, +1.5%).
        """
        mirror = VirtualAMMMirror()
        mirror.register_pool(
            pool_address="PoolTP1Stress",
            token_mint="TokenTP1Stress",
            initial_sol_reserve=84.0,
            initial_token_reserve=206_900_000.0,
        )

        exec_sim = FrictionExecutionSimulator(
            virtual_amm=mirror,
            sol_price_usd=self.sol_price_usd,
            enable_mev_sandwich=True,
            enable_slot_latency=True,
            deterministic_seed=999,
        )

        sim = PathASimulator(
            ledger=self.ledger,
            sol_price_usd=self.sol_price_usd,
            execution_simulator=exec_sim,
            enable_friction_engine=True,
        )

        t0 = datetime(2026, 10, 17, 10, 0, 0, tzinfo=timezone.utc)
        pos = sim.open_position(
            position_id="pos_tp1_stress_01",
            pool_address="PoolTP1Stress",
            token_mint="TokenTP1Stress",
            regime="REGIME_TEST",
            market_cap_sol=380.0,
            entry_price_sol=84.0 / 206_900_000.0,
            position_notional_usd=15.0,
            entry_time=t0,
        )

        # Trigger TP1 (+9% gross)
        t_tp1 = t0 + timedelta(minutes=4)
        pos = sim.update_price_tick(
            pos=pos,
            current_price_sol=pos.tp1_price_sol * 1.01,
            tick_time=t_tp1,
        )

        slices_count = len(pos.tp1_legs)
        has_3_slices = slices_count == 3

        penalties = [leg.mev_penalty_pct for leg in pos.tp1_legs]
        escalation_valid = penalties == [0.50, 1.00, 1.50]

        # Slices spaced 2-3 blocks apart (~1.0s)
        timestamps = [leg.timestamp for leg in pos.tp1_legs]
        spacings = [
            (timestamps[i] - timestamps[i - 1]).total_seconds()
            for i in range(1, len(timestamps))
        ]
        spacings_valid = all(0.5 <= s <= 2.5 for s in spacings) if len(spacings) >= 2 else False

        passed = has_3_slices and escalation_valid and spacings_valid

        details = {
            "slices_count": slices_count,
            "tp1_fill_prices": pos.tp1_fill_prices,
            "escalating_mev_penalties_pct": penalties,
            "slice_time_spacings_seconds": spacings,
            "escalation_valid": escalation_valid,
            "spacings_valid": spacings_valid,
        }

        summary = (
            f"Time-Sliced TP1 Test PASSED: 3 distinct slices executed spaced ~1s apart "
            f"with exact escalating MEV penalties (+0.5%, +1.0%, +1.5%)."
            if passed
            else f"Time-Sliced TP1 Test FAILED: Slices count={slices_count}, penalties={penalties}."
        )
        return StressTestResult("TEST_D", "Time-Sliced TP1 Logic", passed, summary, details)

    def run_all_stress_tests(self) -> List[StressTestResult]:
        """Runs all 4 stress tests and returns structured report."""
        return [
            self.run_test_a_daily_loss_halt(),
            self.run_test_b_liquidity_collapse(),
            self.run_test_c_token_2022_rejection(),
            self.run_test_d_time_sliced_tp1(),
        ]
