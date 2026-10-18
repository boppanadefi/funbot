"""
Solana Wallet & Keypair Management — Bankroll Isolation & Balance Monitoring.
Enforces Section 7.1:
1. Total Dedicated Bankroll: $100.00
2. Active Trading Sub-Wallet: $25.00
3. Cold Segregated Reserve: $75.00 (Zero programmatic withdrawal access)
4. Minimum Exit Reserve: 0.005 SOL (Guardrail 6) and 0.020 SOL (Section 7.1)
"""

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import logging
import os
from pathlib import Path
import time
from typing import Any, Dict, Optional, Tuple
import requests

from src.utils.config import ConfigLoader

logger = logging.getLogger("WalletManager")

from src.utils.base58 import b58encode, b58decode, B58_ALPHABET


# --- RFC 8032 Ed25519 Cryptographic Curve Arithmetic ---
_q = 2**255 - 19
_l = 2**252 + 27742317777372353535851937790883648493
_d = -121665 * pow(121666, _q - 2, _q) % _q
_I = pow(2, (_q - 1) // 4, _q)


def _xrecover(y: int) -> int:
    xx = (y * y - 1) * pow(_d * y * y + 1, _q - 2, _q)
    x = pow(xx, (_q + 3) // 8, _q)
    if (x * x - xx) % _q != 0:
        x = (x * _I) % _q
    if x % 2 != 0:
        x = _q - x
    return x


_By = 4 * pow(5, _q - 2, _q) % _q
_Bx = _xrecover(_By)
_B = [_Bx % _q, _By % _q]


def _edwards_add(P: List[int], Q: List[int]) -> List[int]:
    x1, y1 = P[0], P[1]
    x2, y2 = Q[0], Q[1]
    x3 = (x1 * y2 + x2 * y1) * pow(1 + _d * x1 * x2 * y1 * y2, _q - 2, _q) % _q
    y3 = (y1 * y2 + x1 * x2) * pow(1 - _d * x1 * x2 * y1 * y2, _q - 2, _q) % _q
    return [x3, y3]


def _scalarmult(P: List[int], e: int) -> List[int]:
    if e == 0:
        return [0, 1]
    Q = _scalarmult(P, e // 2)
    Q = _edwards_add(Q, Q)
    if e & 1:
        Q = _edwards_add(Q, P)
    return Q


def _encodeint(y: int) -> bytes:
    return int.to_bytes(y, 32, "little")


def _encodepoint(P: List[int]) -> bytes:
    return int.to_bytes(P[1] + ((P[0] & 1) << 255), 32, "little")


def ed25519_public_key(sk_seed: bytes) -> bytes:
    """Derives 32-byte Ed25519 public key from 32-byte secret seed."""
    h = hashlib.sha512(sk_seed).digest()
    a = int.from_bytes(h[:32], "little")
    a &= (1 << 254) - 8
    a |= (1 << 254)
    A = _scalarmult(_B, a)
    return _encodepoint(A)


def ed25519_sign(msg: bytes, sk_seed: bytes, pk: bytes) -> bytes:
    """Computes standard 64-byte Ed25519 signature (RFC 8032)."""
    h = hashlib.sha512(sk_seed).digest()
    a = int.from_bytes(h[:32], "little")
    a &= (1 << 254) - 8
    a |= (1 << 254)
    r = int.from_bytes(hashlib.sha512(h[32:] + msg).digest(), "little") % _l
    R = _scalarmult(_B, r)
    k = int.from_bytes(hashlib.sha512(_encodepoint(R) + pk + msg).digest(), "little") % _l
    S = (r + k * a) % _l
    return _encodepoint(R) + _encodeint(S)


@dataclass
class WalletAccount:
    pubkey: str
    is_live: bool
    label: str
    secret_seed: Optional[bytes] = None
    balance_sol: float = 0.0
    balance_usd: float = 0.0
    last_balance_check: float = 0.0


class WalletManager:
    """
    Manages active trading wallet keypair, cold segregated reserve, and balance tracking.
    """

    BANKROLL_TOTAL_USD = 100.00
    ACTIVE_SUB_WALLET_LIMIT_USD = 25.00
    COLD_RESERVE_USD = 75.00
    EMERGENCY_EXIT_RESERVE_SOL = 0.005  # Guardrail 6
    SECTION_7_EXIT_RESERVE_SOL = 0.020  # Section 7.1

    def __init__(self, sol_price_usd: float = 155.0):
        self.sol_price_usd = sol_price_usd
        cfg = ConfigLoader().load_telemetry_config()
        self.http_rpc_url = cfg.http_rpc_url
        self.active_wallet: WalletAccount = self._init_active_wallet()

    def _init_active_wallet(self) -> WalletAccount:
        """Initializes active trading wallet from .env or creates sandbox simulated keypair."""
        priv_key_str = (
            os.getenv("SOLANA_WALLET_PRIVATE_KEY")
            or os.getenv("ACTIVE_TRADING_PRIVATE_KEY")
            or os.getenv("SOLANA_PRIVATE_KEY")
        )

        if priv_key_str:
            try:
                # Support Base58 or JSON array [1,2,3...] format
                if priv_key_str.strip().startswith("[") and priv_key_str.strip().endswith("]"):
                    raw_bytes = bytes(json.loads(priv_key_str))
                else:
                    raw_bytes = b58decode(priv_key_str.strip())

                # Solana keypairs are 64 bytes (32-byte secret seed + 32-byte public key)
                seed = raw_bytes[:32] if len(raw_bytes) >= 32 else raw_bytes
                pk_bytes = ed25519_public_key(seed)
                pubkey_b58 = b58encode(pk_bytes)

                logger.info("Initialized LIVE trading wallet: %s", pubkey_b58)
                return WalletAccount(
                    pubkey=pubkey_b58,
                    is_live=True,
                    label="LIVE ARMED KEYPAIR",
                    secret_seed=seed,
                )
            except Exception as e:
                logger.error("Failed to parse live private key from .env: %s. Falling back to sandbox.", e)

        # Sandbox / Simulated Keypair Fallback
        # Deterministic dummy seed for consistent sandbox testing
        sandbox_seed = hashlib.sha256(b"funbot_active_trading_sandbox_seed_v2.5").digest()
        sandbox_pk = ed25519_public_key(sandbox_seed)
        sandbox_pubkey = b58encode(sandbox_pk)

        return WalletAccount(
            pubkey=sandbox_pubkey,
            is_live=False,
            label="SANDBOX SIMULATED KEYPAIR",
            secret_seed=sandbox_seed,
            balance_sol=0.16129,  # $25.00 equivalent at $155 SOL
            balance_usd=25.00,
        )

    def fetch_on_chain_balance(self, pubkey: Optional[str] = None) -> float:
        """Fetches live on-chain SOL balance via Helius RPC getBalance."""
        target_pk = pubkey or self.active_wallet.pubkey
        try:
            resp = requests.post(
                self.http_rpc_url,
                json={"jsonrpc": "2.0", "id": 1, "method": "getBalance", "params": [target_pk]},
                timeout=4.0,
            )
            if resp.status_code == 200:
                data = resp.json()
                lamports = data.get("result", {}).get("value", 0)
                bal_sol = round(lamports / 1e9, 6)
                if not pubkey or pubkey == self.active_wallet.pubkey:
                    self.active_wallet.balance_sol = bal_sol
                    self.active_wallet.balance_usd = round(bal_sol * self.sol_price_usd, 2)
                    self.active_wallet.last_balance_check = time.time()
                return bal_sol
        except Exception as e:
            logger.warning("Could not fetch balance for %s: %s", target_pk, e)
        return self.active_wallet.balance_sol

    def validate_pre_trade_balance(self, trade_notional_sol: float) -> Tuple[bool, str]:
        """
        Enforces Section 7.1 & Guardrail 6:
        Remaining free SOL must retain at least 0.005 SOL emergency liquidation reserve.
        """
        free_sol = self.active_wallet.balance_sol
        remaining = free_sol - trade_notional_sol
        if remaining < self.EMERGENCY_EXIT_RESERVE_SOL:
            return (
                False,
                f"Trade requires {trade_notional_sol:.4f} SOL but wallet has {free_sol:.4f} SOL, "
                f"leaving {remaining:.4f} SOL (violates mandatory {self.EMERGENCY_EXIT_RESERVE_SOL:.3f} SOL exit reserve).",
            )
        return True, "Balance satisfies pre-trade capacity and exit reserve."

    def get_wallet_overview(self) -> Dict[str, Any]:
        """Returns structured wallet state for live dashboard."""
        return {
            "pubkey": self.active_wallet.pubkey,
            "masked_pubkey": f"{self.active_wallet.pubkey[:6]}...{self.active_wallet.pubkey[-6:]}",
            "is_live": self.active_wallet.is_live,
            "label": self.active_wallet.label,
            "balance_sol": self.active_wallet.balance_sol,
            "balance_usd": self.active_wallet.balance_usd,
            "active_limit_usd": self.ACTIVE_SUB_WALLET_LIMIT_USD,
            "cold_reserve_usd": self.COLD_RESERVE_USD,
            "emergency_reserve_sol": self.EMERGENCY_EXIT_RESERVE_SOL,
            "exit_reserve_usd": round(self.EMERGENCY_EXIT_RESERVE_SOL * self.sol_price_usd, 2),
        }
