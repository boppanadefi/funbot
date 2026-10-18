"""
Comprehensive Automated Unit & Integration Tests for Phase 10:
Mainnet Micro-Capital Deployment & Zero-Capital Dry-Run Pipeline.
Verifies:
1. SimulatedWalletContext & Section 7.1 Bankroll Isolation (Zero Real Capital Risk).
2. Jito Bundle Structural Validation (Invariant 6: tip-after-swap, 1.5% tip cap, 0.005 SOL reserve).
3. Helius RPC simulateTransaction Client & Error Handling.
4. NetworkLatencyProfiler & Multi-Region Jito Ranking.
5. End-to-End Dry-Run Pipeline Execution.
"""

from unittest.mock import MagicMock, patch
import json
import pytest
import time

from src.live.dry_run_simulator import (
    DryRunSimulator,
    SimulatedWalletContext,
    SimulationOutcome,
    DryRunReceipt,
)
from src.live.network_latency_profiler import (
    NetworkLatencyProfiler,
    EndpointLatencyMetric,
    NetworkBaselineReport,
)
from src.live.wallet_manager import WalletManager, WalletAccount
from src.live.jito_client import JITO_TIP_ACCOUNTS
from src.protocol.pumpswap import (
    CompiledInstruction,
    AccountMeta,
    PUMPSWAP_PROGRAM_ID_STR,
    SYSTEM_PROGRAM_STR,
)
from src.live.wire_tx_builder import (
    build_set_compute_unit_limit,
    build_set_compute_unit_price,
    build_system_transfer,
)
from scripts.run_dry_run_pipeline import run_dry_run_pipeline


class TestSimulatedWalletContext:
    """Verifies that simulation mode provides strict bankroll isolation without private key."""

    def test_simulation_context_initialization(self):
        ctx = SimulatedWalletContext(pubkey="SimulatedPubkey1111111111111111111111111111")
        assert ctx.is_live is False
        assert ctx.balance_usd == 25.00
        assert ctx.cold_reserve_usd == 75.00
        assert ctx.emergency_reserve_sol == 0.005
        banner = ctx.banner()
        assert "SIMULATION MODE ACTIVE" in banner
        assert "ZERO LIVE CAPITAL AT RISK" in banner

    def test_wallet_manager_falls_back_to_simulated_keypair(self, monkeypatch):
        # Clear any environment variables that contain private keys
        monkeypatch.delenv("SOLANA_WALLET_PRIVATE_KEY", raising=False)
        monkeypatch.delenv("ACTIVE_TRADING_PRIVATE_KEY", raising=False)
        monkeypatch.delenv("SOLANA_PRIVATE_KEY", raising=False)

        wm = WalletManager()
        assert wm.active_wallet.is_live is False
        assert wm.active_wallet.label == "SANDBOX SIMULATED KEYPAIR"
        assert wm.active_wallet.secret_seed is not None
        assert len(wm.active_wallet.pubkey) > 30


class TestJitoBundleStructureValidation:
    """Tests Invariant 6 structural rules on wire instructions."""

    @pytest.fixture
    def simulator(self):
        return DryRunSimulator()

    def test_valid_bundle_structure(self, simulator):
        swap_ix = CompiledInstruction(
            program_id=PUMPSWAP_PROGRAM_ID_STR,
            accounts=[AccountMeta(pubkey="User111", is_signer=True, is_writable=True)],
            data=b"test_swap_data",
        )
        tip_account = JITO_TIP_ACCOUNTS[0]
        tip_ix = build_system_transfer(
            sender="User111",
            recipient=tip_account,
            lamports=100_000,
        )

        instructions = [swap_ix, tip_ix]
        # Notional $3.00 = ~0.019 SOL = 19,000,000 lamports
        # 1.5% max tip = 285,000 lamports
        # Tip = 100,000 lamports (valid)
        # Balance = 161,290,000 lamports (leaves > 0.005 SOL = 5,000,000 lamports)
        valid, status, err = simulator.validate_jito_bundle_structure(
            instructions=instructions,
            notional_lamports=19_000_000,
            tip_lamports=100_000,
            wallet_balance_lamports=161_290_000,
        )
        assert valid is True
        assert status == "PASSED_INVARIANT_6"
        assert err is None

    def test_ordering_violation_tip_before_swap(self, simulator):
        tip_account = JITO_TIP_ACCOUNTS[0]
        tip_ix = build_system_transfer(sender="User111", recipient=tip_account, lamports=100_000)
        swap_ix = CompiledInstruction(
            program_id=PUMPSWAP_PROGRAM_ID_STR,
            accounts=[AccountMeta(pubkey="User111", is_signer=True, is_writable=True)],
            data=b"test_swap_data",
        )
        # Tip placed BEFORE swap
        instructions = [tip_ix, swap_ix]

        valid, status, err = simulator.validate_jito_bundle_structure(
            instructions=instructions,
            notional_lamports=19_000_000,
            tip_lamports=100_000,
            wallet_balance_lamports=161_290_000,
        )
        assert valid is False
        assert status == "ORDERING_VIOLATION"
        assert "MUST follow the swap instruction" in err

    def test_invalid_tip_recipient_account(self, simulator):
        swap_ix = CompiledInstruction(
            program_id=PUMPSWAP_PROGRAM_ID_STR,
            accounts=[AccountMeta(pubkey="User111", is_signer=True, is_writable=True)],
            data=b"test_swap_data",
        )
        # Random non-Jito account
        tip_ix = build_system_transfer(
            sender="User111",
            recipient="SomeRandomAttackerAccount111111111111111111111",
            lamports=100_000,
        )
        instructions = [swap_ix, tip_ix]

        valid, status, err = simulator.validate_jito_bundle_structure(
            instructions=instructions,
            notional_lamports=19_000_000,
            tip_lamports=100_000,
            wallet_balance_lamports=161_290_000,
        )
        assert valid is False
        assert status == "INVALID_STRUCTURE" or status == "INVALID_TIP_RECIPIENT"

    def test_tip_cap_exceeded(self, simulator):
        swap_ix = CompiledInstruction(
            program_id=PUMPSWAP_PROGRAM_ID_STR,
            accounts=[AccountMeta(pubkey="User111", is_signer=True, is_writable=True)],
            data=b"test_swap_data",
        )
        tip_ix = build_system_transfer(
            sender="User111",
            recipient=JITO_TIP_ACCOUNTS[0],
            lamports=500_000,  # 500k lamports on 19M notional = 2.63% > 1.5%
        )
        instructions = [swap_ix, tip_ix]

        valid, status, err = simulator.validate_jito_bundle_structure(
            instructions=instructions,
            notional_lamports=19_000_000,
            tip_lamports=500_000,
            wallet_balance_lamports=161_290_000,
        )
        assert valid is False
        assert status == "TIP_CAP_EXCEEDED"
        assert "exceeds 1.5% notional cap" in err

    def test_insufficient_exit_reserve(self, simulator):
        swap_ix = CompiledInstruction(
            program_id=PUMPSWAP_PROGRAM_ID_STR,
            accounts=[AccountMeta(pubkey="User111", is_signer=True, is_writable=True)],
            data=b"test_swap_data",
        )
        tip_ix = build_system_transfer(
            sender="User111",
            recipient=JITO_TIP_ACCOUNTS[0],
            lamports=100_000,
        )
        instructions = [swap_ix, tip_ix]

        # Balance only leaves 1,000,000 lamports (< 5,000,000 mandatory reserve)
        valid, status, err = simulator.validate_jito_bundle_structure(
            instructions=instructions,
            notional_lamports=19_000_000,
            tip_lamports=100_000,
            wallet_balance_lamports=20_100_000,
        )
        assert valid is False
        assert status == "INSUFFICIENT_RESERVE"
        assert "breaching 0.005 SOL exit reserve" in err


class TestHeliusSimulationClient:
    """Tests Helius JSON-RPC simulateTransaction parsing and responses."""

    @pytest.fixture
    def simulator(self):
        return DryRunSimulator()

    def test_simulate_transaction_success(self, simulator):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "jsonrpc": "2.0",
            "result": {
                "context": {"slot": 298765432},
                "value": {
                    "err": None,
                    "unitsConsumed": 42500,
                    "logs": [
                        "Program ComputeBudget111111111111111111111111111111 invoke [1]",
                        "Program ComputeBudget111111111111111111111111111111 success",
                    ],
                },
            },
            "id": 1,
        }

        with patch("requests.post", return_value=mock_response):
            ok, units, logs, err, latency = simulator.simulate_solana_transaction(b"dummy_wire_bytes")

        assert ok is True
        assert units == 42500
        assert len(logs) == 2
        assert err is None
        assert latency >= 0.0

    def test_simulate_transaction_execution_error(self, simulator):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "jsonrpc": "2.0",
            "result": {
                "context": {"slot": 298765432},
                "value": {
                    "err": {"InstructionError": [2, "Custom(6001)"]},
                    "unitsConsumed": 12000,
                    "logs": ["Program error: custom program error: 0x1771"],
                },
            },
            "id": 1,
        }

        with patch("requests.post", return_value=mock_response):
            ok, units, logs, err, latency = simulator.simulate_solana_transaction(b"dummy_wire_bytes")

        assert ok is False
        assert units == 12000
        assert "Simulation Execution Error" in err

    def test_simulate_transaction_network_failure(self, simulator):
        with patch("requests.post", side_effect=Exception("Connection timed out")):
            ok, units, logs, err, latency = simulator.simulate_solana_transaction(b"dummy_wire_bytes")

        assert ok is False
        assert units == 0
        assert "Network Exception" in err


class TestNetworkLatencyProfiler:
    """Tests multi-region latency baselining and ranking."""

    def test_ping_helius_rpc_healthy(self):
        profiler = NetworkLatencyProfiler()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"jsonrpc": "2.0", "result": "ok", "id": 1}

        with patch("requests.post", return_value=mock_resp):
            metric = profiler.ping_helius_rpc()

        assert metric.name == "Helius RPC"
        assert metric.is_healthy is True
        assert metric.latency_ms >= 0.0

    def test_run_full_baseline_ranks_regions(self, tmp_path):
        profiler = NetworkLatencyProfiler(telemetry_dir=str(tmp_path))

        def mock_post(url, json=None, timeout=None):
            resp = MagicMock()
            resp.status_code = 200
            if "frankfurt" in url:
                time.sleep(0.005)
                resp.json.return_value = {"result": ["tip1", "tip2"]}
            elif "tokyo" in url:
                time.sleep(0.025)
                resp.json.return_value = {"result": ["tip1", "tip2"]}
            else:
                resp.json.return_value = {"result": ["tip1"]}
            return resp

        with patch("requests.post", side_effect=mock_post):
            report = profiler.run_full_baseline()

        assert isinstance(report, NetworkBaselineReport)
        assert len(report.jito_regions_ranked) == 5
        assert report.helius_healthy is True
        # Verify persistence to disk
        baseline_file = tmp_path / "network_baselines.json"
        assert baseline_file.exists()
        with open(baseline_file, "r") as f:
            data = json.load(f)
            assert "fastest_jito_region" in data
            assert len(data["jito_regions_ranked"]) == 5


class TestDryRunPipelineIntegration:
    """Verifies end-to-end operator CLI pipeline in simulation mode."""

    def test_run_dry_run_pipeline_cli_execution(self, monkeypatch):
        # Guarantee simulation mode
        monkeypatch.delenv("SOLANA_WALLET_PRIVATE_KEY", raising=False)

        mock_sim_resp = MagicMock()
        mock_sim_resp.status_code = 200
        mock_sim_resp.json.return_value = {
            "jsonrpc": "2.0",
            "result": {
                "value": {
                    "err": None,
                    "unitsConsumed": 38400,
                    "logs": ["Program success"],
                }
            },
            "id": 1,
        }

        mock_bh_resp = MagicMock()
        mock_bh_resp.status_code = 200
        mock_bh_resp.json.return_value = {
            "jsonrpc": "2.0",
            "result": {"value": {"blockhash": "4uQeVj5tqViQh7yWWGStvkEG1Zmhx6uasJtWCJziofM"}},
            "id": 1,
        }

        def mock_post(url, json=None, timeout=None):
            method = json.get("method") if json else ""
            if method == "simulateTransaction":
                return mock_sim_resp
            elif method == "getLatestBlockhash":
                return mock_bh_resp
            else:
                resp = MagicMock()
                resp.status_code = 200
                resp.json.return_value = {"result": ["tip1"]}
                return resp

        with patch("requests.post", side_effect=mock_post):
            receipt = run_dry_run_pipeline()

        assert isinstance(receipt, DryRunReceipt)
        assert receipt.outcome == SimulationOutcome.SUCCESS
        assert receipt.jito_validation_status == "PASSED_INVARIANT_6"
        assert receipt.units_consumed == 38400
        assert receipt.tip_cap_pct <= 1.50
