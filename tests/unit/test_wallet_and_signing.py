"""
Unit tests for Solana Wallet Manager, Transaction Signer, and Jito Dispatcher.
"""

from unittest.mock import patch, MagicMock
import pytest

from src.live.jito_bundle_builder import JitoBundleBuilder
from src.live.jito_dispatcher import JitoTransactionDispatcher
from src.live.transaction_signer import SolanaTransactionSigner
from src.live.wallet_manager import WalletManager, b58encode, b58decode, ed25519_public_key, ed25519_sign


def test_base58_codec():
    data = b"\x00\x01\x02\x03\xff\xfe\xfd"
    encoded = b58encode(data)
    decoded = b58decode(encoded)
    assert decoded == data

    # Zero padding
    zero_data = b"\x00\x00\x00abc"
    enc_zero = b58encode(zero_data)
    assert enc_zero.startswith("111")
    assert b58decode(enc_zero) == zero_data


def test_ed25519_key_derivation_and_signature():
    seed = b"test_seed_32_bytes_long_exact!!"
    pk = ed25519_public_key(seed)
    assert len(pk) == 32

    msg = b"solana_swap_instruction_payload"
    sig = ed25519_sign(msg, seed, pk)
    assert len(sig) == 64


def test_wallet_manager_initialization():
    wm = WalletManager()
    overview = wm.get_wallet_overview()
    assert overview["pubkey"] is not None
    assert overview["active_limit_usd"] == 25.00
    assert overview["cold_reserve_usd"] == 75.00
    assert overview["emergency_reserve_sol"] == 0.005


def test_wallet_pre_trade_balance_check():
    wm = WalletManager()
    wm.active_wallet.balance_sol = 0.020  # Has 0.02 SOL
    # Trade of 0.010 leaves 0.010 > 0.005 -> Passes
    ok, msg = wm.validate_pre_trade_balance(0.010)
    assert ok is True

    # Trade of 0.018 leaves 0.002 < 0.005 -> Fails
    ok, msg = wm.validate_pre_trade_balance(0.018)
    assert ok is False
    assert "exit reserve" in msg


def test_transaction_signer_signs_jito_bundle():
    wm = WalletManager()
    signer = SolanaTransactionSigner(wallet_manager=wm)
    builder = JitoBundleBuilder()

    bundle = builder.build_atomic_swap_bundle(
        pool_address="PoolSignTest11111111111111111111111111111",
        token_mint="MintSignTest11111111111111111111111111111",
        is_buy=True,
        position_notional_sol=0.10,
        proposed_tip_sol=0.001,
        user_wallet_pubkey=wm.active_wallet.pubkey,
    )

    signed_bundle = signer.sign_atomic_jito_bundle(bundle)
    assert signed_bundle.bundle_id == bundle.bundle_id
    assert len(signed_bundle.signed_transactions) == 4  # 4 ordered instructions
    assert all(len(tx.signature_b58) > 20 for tx in signed_bundle.signed_transactions)


def test_jito_dispatcher_simulated_dispatch():
    wm = WalletManager()
    signer = SolanaTransactionSigner(wallet_manager=wm)
    builder = JitoBundleBuilder()
    dispatcher = JitoTransactionDispatcher()

    bundle = builder.build_atomic_swap_bundle(
        pool_address="PoolDispatchTest1111111111111111111111111",
        token_mint="MintDispatchTest1111111111111111111111111",
        is_buy=True,
        position_notional_sol=0.05,
        proposed_tip_sol=0.0005,
        user_wallet_pubkey=wm.active_wallet.pubkey,
    )
    signed_bundle = signer.sign_atomic_jito_bundle(bundle)

    receipt = dispatcher.dispatch_signed_bundle(signed_bundle, simulate_only=True)
    assert receipt.success is True
    assert receipt.broadcast_target == "SIMULATED_SANDBOX"
    assert len(receipt.transaction_signatures) == 4
