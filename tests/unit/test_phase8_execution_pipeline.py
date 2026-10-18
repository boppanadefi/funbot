"""
Unit Test Suite for Phase 8: Execution & MEV Pipeline.
Covers:
1. Wire Transaction Construction & Serialization (todo-02)
2. Jito Multi-Region Bundle Pipeline & Invariant 6 Enforcement (todo-12)
3. Transaction Confirmation Monitoring & Latency Profiling (todo-10)
"""

import asyncio
import struct
import time
import pytest

from src.live.wire_tx_builder import (
    SolanaWireTransactionBuilder,
    BlockhashManager,
    encode_compact_u16,
    decode_compact_u16,
    build_set_compute_unit_limit,
    build_set_compute_unit_price,
    build_create_ata_idempotent,
    build_system_transfer,
    COMPUTE_BUDGET_PROGRAM_STR,
)
from src.live.jito_client import (
    JitoClient,
    TipCapExceededError,
    InsufficientReserveError,
    JITO_TIP_ACCOUNTS,
    JITO_REGIONS,
)
from src.live.confirmation_monitor import (
    ConfirmationMonitor,
    ConfirmationStatus,
    ConfirmationOutcome,
)
from src.live.wallet_manager import WalletAccount, b58encode, b58decode
from src.protocol.pumpswap import PumpSwapProgram


# =====================================================================
# 1. Wire Transaction Builder Tests (todo-02)
# =====================================================================

class TestSolanaWireTransactionBuilder:
    def setup_method(self):
        self.dummy_wallet = WalletAccount(
            pubkey="71kM3Wc7hS4nFzYt8Q2n5K1p9M6a4v8e3r1t7y9u5i2o",
            is_live=False,
            label="Test Active Wallet",
            secret_seed=b"\x07" * 32,
        )
        self.blockhash = "5t1C1mN2a2mC7Z9W6d7T8H5h9L4p3Q2n1M8v6K4r2E1w"

    def test_compact_u16_codec(self):
        """Verify Solana compact-u16 encoder and decoder."""
        test_values = [0, 1, 127, 128, 255, 16383, 16384, 65535]
        for val in test_values:
            encoded = encode_compact_u16(val)
            decoded, offset = decode_compact_u16(encoded, 0)
            assert decoded == val
            assert offset == len(encoded)

    def test_compute_budget_instruction_builders(self):
        """Verify ComputeBudget unit limit and unit price instruction encoding."""
        ix_limit = build_set_compute_unit_limit(250_000)
        assert ix_limit.program_id == COMPUTE_BUDGET_PROGRAM_STR
        assert len(ix_limit.data) == 5
        tag, units = struct.unpack("<BI", ix_limit.data)
        assert tag == 2
        assert units == 250_000

        ix_price = build_set_compute_unit_price(15_000)
        assert ix_price.program_id == COMPUTE_BUDGET_PROGRAM_STR
        assert len(ix_price.data) == 9
        tag, price = struct.unpack("<BQ", ix_price.data)
        assert tag == 3
        assert price == 15_000

    def test_create_ata_idempotent_builder(self):
        """Verify createAssociatedTokenAccountIdempotent instruction encoding."""
        ix_ata = build_create_ata_idempotent(
            payer=self.dummy_wallet.pubkey,
            owner=self.dummy_wallet.pubkey,
            mint="MintDezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263",
        )
        assert len(ix_ata.accounts) == 6
        assert ix_ata.accounts[0].pubkey == self.dummy_wallet.pubkey
        assert ix_ata.accounts[0].is_signer is True
        assert ix_ata.data == bytes([1])

    def test_build_signed_wire_transaction(self):
        """Verify wire transaction serialization and Ed25519 signature header."""
        ix1 = build_set_compute_unit_limit(200_000)
        ix2 = build_set_compute_unit_price(10_000)

        wire_bytes, wire_b58, sig_b58 = SolanaWireTransactionBuilder.build_signed_wire_transaction(
            wallet=self.dummy_wallet,
            instructions=[ix1, ix2],
            recent_blockhash=self.blockhash,
        )

        assert isinstance(wire_bytes, bytes)
        assert len(wire_bytes) > 100
        assert len(wire_b58) > 100
        assert len(sig_b58) > 80

        # Wire header: first byte is compact-u16 num_signatures (1)
        assert wire_bytes[0] == 1
        # Followed by 64-byte signature
        sig_extracted = wire_bytes[1:65]
        assert b58encode(sig_extracted) == sig_b58

    def test_blockhash_manager_caching(self):
        """Verify blockhash manager caches and returns valid blockhash."""
        bm = BlockhashManager(rpc_url=None, ttl_seconds=10.0)
        bh1 = bm.get_recent_blockhash()
        assert len(bh1) > 30
        bh2 = bm.get_recent_blockhash()
        assert bh1 == bh2


# =====================================================================
# 2. Jito Client & Invariant 6 Enforcement Tests (todo-12)
# =====================================================================

class TestJitoClient:
    def setup_method(self):
        self.jito = JitoClient()
        self.dummy_wallet = WalletAccount(
            pubkey="71kM3Wc7hS4nFzYt8Q2n5K1p9M6a4v8e3r1t7y9u5i2o",
            is_live=False,
            label="Test Active Wallet",
            secret_seed=b"\x07" * 32,
        )

    def test_canonical_tip_accounts_and_regions(self):
        """Verify 8 canonical Jito tip accounts and 5 regions exist."""
        assert len(JITO_TIP_ACCOUNTS) == 8
        assert "default" in JITO_REGIONS
        assert "frankfurt" in JITO_REGIONS
        assert "amsterdam" in JITO_REGIONS
        assert "ny" in JITO_REGIONS
        assert "tokyo" in JITO_REGIONS

    def test_invariant6_instruction_ordering(self):
        """Verify Invariant 6: tip instruction MUST be placed strictly AFTER swap instruction."""
        program = PumpSwapProgram()
        swap_ix = program.build_buy_instruction(
            user_address=self.dummy_wallet.pubkey,
            mint_address="MintDezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263",
            amount_tokens=10_000_000,
            max_sol_cost_lamports=100_000_000,
        )

        tip_recipient = self.jito.get_random_tip_account()
        instructions = self.jito.assemble_trade_instructions(
            payer=self.dummy_wallet.pubkey,
            swap_instruction=swap_ix,
            tip_lamports=100_000,
            tip_recipient=tip_recipient,
        )

        # Instructions: [ComputeLimit, ComputePrice, Swap, Tip]
        assert len(instructions) == 4
        # Swap is index 2, Tip is index 3
        assert instructions[2].program_id == program.program_id
        assert instructions[3].program_id == "11111111111111111111111111111111"
        assert instructions[3].accounts[1].pubkey == tip_recipient

    def test_invariant6_tip_cap_exceeded_error(self):
        """Verify Invariant 6: tips exceeding 1.5% notional raise TipCapExceededError."""
        notional_lamports = 100_000_000  # 0.10 SOL
        wallet_lamports = 500_000_000    # 0.50 SOL

        # Max tip = 1.5% of 100M = 1.5M lamports
        valid_tip = 1_000_000
        assert self.jito.validate_tip_budget(valid_tip, notional_lamports, wallet_lamports) == valid_tip

        # 2M lamports exceeds 1.5% cap -> must raise
        excessive_tip = 2_000_000
        with pytest.raises(TipCapExceededError, match="exceeds 1.5% notional cap"):
            self.jito.validate_tip_budget(excessive_tip, notional_lamports, wallet_lamports)

    def test_invariant6_exit_reserve_violation_error(self):
        """Verify Invariant 6: trades violating 0.005 SOL exit reserve raise InsufficientReserveError."""
        notional_lamports = 100_000_000  # 0.10 SOL
        tip_lamports = 1_000_000         # 0.001 SOL
        # Total required = 0.101 SOL + 0.005 SOL reserve = 0.106 SOL (106,000,000 lamports)
        # Wallet only has 103,000,000 lamports -> fails reserve requirement
        short_wallet = 103_000_000
        with pytest.raises(InsufficientReserveError, match="drops below required 0.005 SOL exit reserve"):
            self.jito.validate_tip_budget(tip_lamports, notional_lamports, short_wallet)

    def test_send_bundle_simulation_mode(self):
        """Verify send_bundle in simulation mode returns immediate acceptance receipt."""
        receipt = self.jito.send_bundle(
            wire_transactions_b58=["dummyWireTransactionB58String1234567890"],
            simulate_only=True,
        )
        assert receipt.success is True
        assert receipt.status == "SIMULATED_ACCEPTED"
        assert receipt.region == "simulated"


# =====================================================================
# 3. Transaction Confirmation Monitor Tests (todo-10)
# =====================================================================

class TestConfirmationMonitor:
    @pytest.mark.asyncio
    async def test_wait_for_confirmation_success(self):
        """Verify confirmation monitor correctly captures confirmed status."""
        monitor = ConfirmationMonitor(rpc_url=None, timeout_sec=2.0)
        outcome = await monitor.wait_for_confirmation(
            signature="SigConfirmed111111111111111111111111111111111111",
            simulated_outcome=ConfirmationStatus.CONFIRMED,
            simulated_slot=458325200,
        )
        assert outcome.status == ConfirmationStatus.CONFIRMED
        assert outcome.slot_landed == 458325200
        assert outcome.compute_units_consumed == 185_000
        assert outcome.error_message is None

    @pytest.mark.asyncio
    async def test_wait_for_confirmation_failed_on_chain(self):
        """Verify confirmation monitor captures on-chain revert."""
        monitor = ConfirmationMonitor(rpc_url=None, timeout_sec=2.0)
        outcome = await monitor.wait_for_confirmation(
            signature="SigFailed111111111111111111111111111111111111",
            simulated_outcome=ConfirmationStatus.FAILED_ON_CHAIN,
        )
        assert outcome.status == ConfirmationStatus.FAILED_ON_CHAIN
        assert outcome.error_message is not None

    @pytest.mark.asyncio
    async def test_wait_for_confirmation_timeout_expired(self):
        """Verify confirmation monitor times out and emits EXPIRED status."""
        monitor = ConfirmationMonitor(rpc_url=None, timeout_sec=0.05)
        # Call with simulated_outcome=None so loop runs and times out
        # Mock check_signature_status_http returning PENDING
        monitor.check_signature_status_http = lambda sig: (ConfirmationStatus.PENDING, None, None)
        monitor.poll_interval_sec = 0.02

        outcome = await monitor.wait_for_confirmation(
            signature="SigExpired111111111111111111111111111111111111",
            submission_time=time.time(),
        )
        assert outcome.status == ConfirmationStatus.EXPIRED
        assert "expired after" in (outcome.error_message or "")
