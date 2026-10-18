"""
Unit Tests for Phase 3: The Paper Ledger (Strict Accounting).
Verifies:
1. Mathematical precision for every recorded trade:
   - signal_ts: Timestamp strategy triggered.
   - entry_sim_ts: Timestamp the simulated transaction "landed" (signal_ts + slot latency delay).
   - entry_price: Exact AMM fill price after MEV sandwich penalty.
   - position_size_sol: Exact SOL spent including entry fees.
   - tp1_fill_prices: Array of 3 prices for the 3x 30% time-sliced exits.
   - runner_exit_price: Fill price of the final 10%.
   - net_pnl_sol: (SOL received across all exits) - (SOL spent on entry) - (All TX fees) - (All Jito tips).
2. Zero approximations in ledger accounting.
3. Persistent SQLite retrieval via PaperLedger.get_strict_accounting_records().
"""

from datetime import datetime, timezone, timedelta
import json
import pytest

from src.engine.execution_simulator import FrictionExecutionSimulator
from src.engine.models import PositionState, PaperPosition
from src.engine.paper_ledger import PaperLedger
from src.engine.simulator import PathASimulator
from src.engine.virtual_amm import VirtualAMMMirror


@pytest.fixture
def temp_ledger(tmp_path):
    return PaperLedger(db_path=str(tmp_path / "strict_ledger.db"))


class TestStrictAccountingLedger:
    """Verifies Phase 3 strict accounting ledger guarantees."""

    def test_strict_accounting_scaleout_trade(self, temp_ledger):
        """
        Executes a complete 90/10 scale-out trade:
        - Entry with slot latency and front-run sandwich.
        - TP1 execution (3 slices of 30% with escalating MEV penalties).
        - Runner exit of final 10%.
        Validates all 7 required Phase 3 states and mathematical identity of net_pnl_sol.
        """
        mirror = VirtualAMMMirror()
        pool = mirror.register_pool(
            pool_address="PoolStrict01",
            token_mint="TokenStrict01",
            initial_sol_reserve=84.0,
            initial_token_reserve=206_900_000.0,
        )

        exec_sim = FrictionExecutionSimulator(
            virtual_amm=mirror,
            sol_price_usd=155.0,
            enable_mev_sandwich=True,
            enable_slot_latency=True,
            deterministic_seed=42,
        )

        sim = PathASimulator(
            ledger=temp_ledger,
            sol_price_usd=155.0,
            execution_simulator=exec_sim,
            enable_friction_engine=True,
        )

        signal_time = datetime(2026, 10, 17, 10, 0, 0, tzinfo=timezone.utc)
        pos = sim.open_position(
            position_id="pos_strict_scaleout_01",
            pool_address="PoolStrict01",
            token_mint="TokenStrict01",
            regime="REGIME_A",
            market_cap_sol=380.0,
            entry_price_sol=pool.marginal_price_sol,
            position_notional_usd=15.0,
            entry_time=signal_time,
        )

        # 1. State: signal_ts vs entry_sim_ts
        assert pos.signal_ts == signal_time
        assert pos.entry_sim_ts is not None
        assert pos.entry_sim_ts > signal_time
        latency_ms = (pos.entry_sim_ts - pos.signal_ts).total_seconds() * 1000.0
        assert 400.0 <= latency_ms <= 2400.0

        # 2. State: entry_price (exact AMM fill price after MEV penalty)
        assert pos.entry_price_sol > (84.0 / 206_900_000.0)
        assert pos.entry_leg.price_sol == pos.entry_price_sol

        # 3. State: position_size_sol (exact SOL spent including fees)
        expected_entry_friction = (
            pos.entry_leg.venue_fee_sol
            + pos.entry_leg.jito_tip_sol
            + pos.entry_leg.priority_fee_sol
            + pos.entry_leg.base_fee_sol
        )
        assert pos.position_size_sol == round(pos.sol_spent + expected_entry_friction, 6)

        # Execute TP1 (3x 30% time slices)
        tp1_trigger_time = signal_time + timedelta(minutes=3)
        pos = sim.update_price_tick(
            pos=pos,
            current_price_sol=pos.tp1_price_sol * 1.02,
            tick_time=tp1_trigger_time,
        )
        assert pos.state == PositionState.RUNNER_ACTIVE
        assert pos.tp1_activated is True
        assert len(pos.tp1_legs) == 3

        # 4. State: tp1_fill_prices (array of 3 prices)
        tp1_prices = pos.tp1_fill_prices
        assert len(tp1_prices) == 3
        for price in tp1_prices:
            assert price > 0.0
        # Escalating MEV penalty ensures slices degrade in fill price
        assert tp1_prices[0] > tp1_prices[1] > tp1_prices[2]

        # Execute Runner Exit (10% remainder hit trailing stop)
        runner_exit_time = signal_time + timedelta(minutes=5)
        closed_pos = sim.update_price_tick(
            pos=pos,
            current_price_sol=pos.current_stop_price_sol * 0.99,
            tick_time=runner_exit_time,
        )
        assert closed_pos.state == PositionState.CLOSED_TRAIL
        assert closed_pos.runner_leg is not None

        # 5. State: runner_exit_price (fill price of final 10%)
        assert closed_pos.runner_exit_price == closed_pos.runner_leg.price_sol
        assert closed_pos.runner_exit_price > 0.0

        # 6. State: net_pnl_sol formula verification
        # Formula: (SOL received across all exits) - (SOL spent on entry) - (All TX fees) - (All Jito tips)
        sol_received_all_exits = closed_pos.tp1_leg.sol_amount + closed_pos.runner_leg.sol_amount
        sol_spent_entry = closed_pos.sol_spent
        all_tx_fees_sol = (
            closed_pos.entry_leg.venue_fee_sol
            + closed_pos.entry_leg.priority_fee_sol
            + closed_pos.entry_leg.base_fee_sol
            + closed_pos.tp1_leg.venue_fee_sol
            + closed_pos.tp1_leg.priority_fee_sol
            + closed_pos.tp1_leg.base_fee_sol
            + closed_pos.runner_leg.venue_fee_sol
            + closed_pos.runner_leg.priority_fee_sol
            + closed_pos.runner_leg.base_fee_sol
        )
        all_jito_tips_sol = (
            closed_pos.entry_leg.jito_tip_sol
            + closed_pos.tp1_leg.jito_tip_sol
            + closed_pos.runner_leg.jito_tip_sol
        )

        expected_net_pnl_sol = sol_received_all_exits - sol_spent_entry - all_tx_fees_sol - all_jito_tips_sol

        # Check ledger record
        records = temp_ledger.get_strict_accounting_records()
        assert len(records) == 1
        rec = records[0]

        assert rec["position_id"] == "pos_strict_scaleout_01"
        assert rec["signal_ts"] == signal_time.isoformat()
        assert rec["entry_sim_ts"] == pos.entry_sim_ts.isoformat()
        assert rec["entry_price"] == round(pos.entry_price_sol, 8)
        assert rec["position_size_sol"] == pos.position_size_sol
        assert rec["tp1_fill_prices"] == tp1_prices
        assert rec["runner_exit_price"] == round(closed_pos.runner_exit_price, 8)
        assert rec["net_pnl_sol"] == round(expected_net_pnl_sol, 6)
        assert rec["all_tx_fees_sol"] == round(all_tx_fees_sol, 6)
        assert rec["all_jito_tips_sol"] == round(all_jito_tips_sol, 6)

    def test_strict_accounting_stopped_out_trade(self, temp_ledger):
        """
        Executes a trade stopped out before TP1 (e.g. Hard Stop -10%):
        - tp1_fill_prices is empty [].
        - runner_exit_price is None.
        - net_pnl_sol strictly equals (SOL received) - (SOL spent) - (All TX fees) - (All Jito tips).
        """
        mirror = VirtualAMMMirror()
        pool = mirror.register_pool(
            pool_address="PoolStrictStop",
            token_mint="TokenStrictStop",
            initial_sol_reserve=84.0,
            initial_token_reserve=206_900_000.0,
        )

        exec_sim = FrictionExecutionSimulator(
            virtual_amm=mirror,
            sol_price_usd=155.0,
            enable_mev_sandwich=False,
            enable_slot_latency=True,
            deterministic_seed=123,
        )

        sim = PathASimulator(
            ledger=temp_ledger,
            sol_price_usd=155.0,
            execution_simulator=exec_sim,
            enable_friction_engine=True,
        )

        signal_time = datetime(2026, 10, 17, 11, 0, 0, tzinfo=timezone.utc)
        pos = sim.open_position(
            position_id="pos_strict_stop_01",
            pool_address="PoolStrictStop",
            token_mint="TokenStrictStop",
            regime="REGIME_A",
            market_cap_sol=380.0,
            entry_price_sol=pool.marginal_price_sol,
            position_notional_usd=15.0,
            entry_time=signal_time,
        )

        # Price drops directly to hard stop (-10%)
        stop_time = signal_time + timedelta(minutes=2)
        closed_pos = sim.update_price_tick(
            pos=pos,
            current_price_sol=pos.current_stop_price_sol * 0.98,
            tick_time=stop_time,
        )
        assert closed_pos.state == PositionState.CLOSED_STOP
        assert closed_pos.tp1_activated is False
        assert closed_pos.tp1_fill_prices == []
        assert closed_pos.runner_exit_price is None

        # Verify strict accounting formula for single exit
        sol_received = closed_pos.exit_leg.sol_amount
        sol_spent = closed_pos.sol_spent
        all_tx_fees_sol = (
            closed_pos.entry_leg.venue_fee_sol
            + closed_pos.entry_leg.priority_fee_sol
            + closed_pos.entry_leg.base_fee_sol
            + closed_pos.exit_leg.venue_fee_sol
            + closed_pos.exit_leg.priority_fee_sol
            + closed_pos.exit_leg.base_fee_sol
        )
        all_jito_tips_sol = closed_pos.entry_leg.jito_tip_sol + closed_pos.exit_leg.jito_tip_sol
        expected_net_pnl = sol_received - sol_spent - all_tx_fees_sol - all_jito_tips_sol

        records = temp_ledger.get_strict_accounting_records()
        assert len(records) == 1
        rec = records[0]

        assert rec["position_id"] == "pos_strict_stop_01"
        assert rec["tp1_fill_prices"] == []
        assert rec["runner_exit_price"] is None
        assert rec["net_pnl_sol"] == round(expected_net_pnl, 6)
        assert rec["net_pnl_sol"] < 0.0  # Loss must be negative

    def test_ledger_auto_migration_preserves_existing_data(self, tmp_path):
        """
        Verifies that PaperLedger seamlessly migrates existing tables that lack Phase 3 columns
        without operational errors or data loss.
        """
        import sqlite3
        db_file = tmp_path / "legacy.db"

        # Create legacy table without Phase 3 columns
        with sqlite3.connect(str(db_file)) as conn:
            conn.execute("""
            CREATE TABLE paper_trades (
                position_id TEXT PRIMARY KEY,
                pool_address TEXT NOT NULL,
                token_mint TEXT NOT NULL,
                regime TEXT NOT NULL,
                entry_ts TEXT NOT NULL,
                exit_ts TEXT NOT NULL,
                entry_price_sol REAL NOT NULL,
                exit_price_sol REAL NOT NULL,
                sol_spent REAL NOT NULL,
                sol_received REAL NOT NULL,
                venue_fees_sol REAL NOT NULL,
                tips_sol REAL NOT NULL,
                priority_fees_sol REAL NOT NULL,
                net_pnl_sol REAL NOT NULL,
                net_pnl_usd REAL NOT NULL,
                net_return_pct REAL NOT NULL,
                exit_reason TEXT NOT NULL,
                ratchet_activated INTEGER NOT NULL,
                duration_minutes REAL NOT NULL
            );
            """)
            conn.execute("""
            INSERT INTO paper_trades VALUES (
                'pos_legacy_01', 'PoolLegacy', 'MintLegacy', 'REGIME_A',
                '2026-10-17T08:00:00+00:00', '2026-10-17T08:05:00+00:00',
                0.00040, 0.00045, 0.096774, 0.108000,
                0.0012, 0.0050, 0.0003, 0.004726, 0.73, 4.88,
                'TRAILING_STOP', 1, 5.0
            );
            """)
            conn.commit()

        # Initialize PaperLedger on legacy DB
        ledger = PaperLedger(db_path=str(db_file))
        strict_records = ledger.get_strict_accounting_records()
        assert len(strict_records) == 1
        rec = strict_records[0]
        assert rec["position_id"] == "pos_legacy_01"
        assert rec["signal_ts"] == "2026-10-17T08:00:00+00:00"
        assert rec["entry_price"] == 0.00040
        assert rec["tp1_fill_prices"] == []
