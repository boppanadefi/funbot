"""
Jito Multi-Region Block Engine Client & Atomic Bundle Assembler.
Enforces Invariant 6:
1. Strict Instruction Ordering: Tip instruction placed strictly AFTER the swap instruction.
2. 1.5% Notional Tip Cap.
3. Dedicated 0.005 SOL Emergency Exit Reserve.
4. Multi-Region Failover across Frankfurt, Amsterdam, NY, and Tokyo.
"""

from dataclasses import dataclass, field
import json
import logging
import random
import time
from typing import Any, Dict, List, Optional, Tuple
import requests

from src.protocol.pumpswap import CompiledInstruction
from src.live.wire_tx_builder import (
    SolanaWireTransactionBuilder,
    build_set_compute_unit_limit,
    build_set_compute_unit_price,
    build_system_transfer,
)
from src.live.wallet_manager import WalletAccount

logger = logging.getLogger("JitoClient")

# The 8 Canonical Jito Tip Accounts on Solana Mainnet
JITO_TIP_ACCOUNTS: List[str] = [
    "96gYZGLnJYVFmbjzopPSU6QiEV5fGqZNyN9nmNhvrZU5",
    "HFqU5x63VTqvQss8hp11i4wVV8bD44PvwucfZ2bU7gRe",
    "Cw8CFyM9FkoMi7K7Crf6HNQqf4uEMzpKw6QNghXLvLkY",
    "ADaUMid9yfUytqMBgopwjb2DTLSokTSzL1zt6iGPaS49",
    "DfXygSm4jCyNCybVYYK6DwvWqjKee8pbDmJGcLWNDXjh",
    "ADuUkR4vqLUMWXxW9gh6D6L8pWHLnjvnwkvdDxjzPw48",
    "DttWaMuVvTiduZRnguLF7jNxTgiMBZ1hyAumKUiL2KRL",
    "3AVi9Tg9Uo68tJfuvoKvqKNWKkC5wPdSSdeBnizKZ6jT",
]

JITO_REGIONS: Dict[str, str] = {
    "default": "https://mainnet.block-engine.jito.wtf/api/v1/bundles",
    "frankfurt": "https://frankfurt.mainnet.block-engine.jito.wtf/api/v1/bundles",
    "amsterdam": "https://amsterdam.mainnet.block-engine.jito.wtf/api/v1/bundles",
    "ny": "https://ny.mainnet.block-engine.jito.wtf/api/v1/bundles",
    "tokyo": "https://tokyo.mainnet.block-engine.jito.wtf/api/v1/bundles",
}


class TipCapExceededError(ValueError):
    """Raised when Jito tip exceeds 1.5% of position notional."""
    pass


class InsufficientReserveError(ValueError):
    """Raised when transaction would violate the 0.005 SOL exit reserve."""
    pass


@dataclass
class BundleSubmissionResult:
    success: bool
    bundle_id: str
    region: str
    signature: str
    tip_lamports: int
    tip_account: str
    status: str
    error: Optional[str] = None
    timestamp: float = field(default_factory=time.time)


class JitoClient:
    """Production Jito MEV Client with multi-region failover and Invariant 6 compliance."""

    MAX_TIP_PCT_NOTIONAL: float = 1.50  # 1.5% notional cap
    MIN_EXIT_RESERVE_LAMPORTS: int = 5_000_000  # 0.005 SOL

    def __init__(self, primary_region: str = "default", timeout_sec: float = 4.0):
        self.primary_region = primary_region
        self.timeout_sec = timeout_sec
        self._active_regions = list(JITO_REGIONS.keys())

    def get_random_tip_account(self) -> str:
        """Returns a random canonical Jito tip account."""
        return random.choice(JITO_TIP_ACCOUNTS)

    def validate_tip_budget(
        self,
        tip_lamports: int,
        notional_lamports: int,
        wallet_balance_lamports: int,
    ) -> int:
        """
        Validates tip does not exceed 1.5% notional and protects 0.005 SOL reserve.
        Returns clamped valid tip_lamports.
        """
        max_allowed_tip = int(notional_lamports * (self.MAX_TIP_PCT_NOTIONAL / 100.0))
        if tip_lamports > max_allowed_tip:
            raise TipCapExceededError(
                f"Requested tip {tip_lamports} lamports exceeds 1.5% notional cap ({max_allowed_tip} lamports)"
            )

        post_trade_balance = wallet_balance_lamports - notional_lamports - tip_lamports
        if post_trade_balance < self.MIN_EXIT_RESERVE_LAMPORTS:
            raise InsufficientReserveError(
                f"Balance after trade ({post_trade_balance / 1e9:.4f} SOL) drops below required 0.005 SOL exit reserve"
            )

        return tip_lamports

    def assemble_trade_instructions(
        self,
        payer: str,
        swap_instruction: CompiledInstruction,
        tip_lamports: int,
        tip_recipient: str,
        compute_unit_limit: int = 250_000,
        compute_unit_price: int = 10_000,
        setup_instruction: Optional[CompiledInstruction] = None,
    ) -> List[CompiledInstruction]:
        """
        Assembles complete transaction instructions enforcing Invariant 6:
        1. ComputeBudget limits & priority fees
        2. Optional setup instruction (e.g. create ATA)
        3. PumpSwap swap instruction
        4. Jito tip instruction (STRICTLY AFTER SWAP)
        """
        instructions = [
            build_set_compute_unit_limit(compute_unit_limit),
            build_set_compute_unit_price(compute_unit_price),
        ]

        if setup_instruction:
            instructions.append(setup_instruction)

        # 1. SWAP INSTRUCTION FIRST
        instructions.append(swap_instruction)

        # 2. TIP INSTRUCTION STRICTLY AFTER SWAP (Invariant 6)
        tip_ix = build_system_transfer(sender=payer, recipient=tip_recipient, lamports=tip_lamports)
        instructions.append(tip_ix)

        return instructions

    def assemble_and_sign_bundle(
        self,
        wallet: WalletAccount,
        swap_instruction: CompiledInstruction,
        notional_lamports: int,
        tip_lamports: int,
        recent_blockhash: str,
        wallet_balance_lamports: int,
        setup_instruction: Optional[CompiledInstruction] = None,
    ) -> Tuple[str, str, str, int]:
        """
        Validates tip, orders instructions (swap then tip), and signs wire transaction.
        Returns (wire_b58, signature_b58, tip_recipient, valid_tip).
        """
        valid_tip = self.validate_tip_budget(tip_lamports, notional_lamports, wallet_balance_lamports)
        tip_recipient = self.get_random_tip_account()

        instructions = self.assemble_trade_instructions(
            payer=wallet.pubkey,
            swap_instruction=swap_instruction,
            tip_lamports=valid_tip,
            tip_recipient=tip_recipient,
            setup_instruction=setup_instruction,
        )

        _, wire_b58, sig_b58 = SolanaWireTransactionBuilder.build_signed_wire_transaction(
            wallet=wallet,
            instructions=instructions,
            recent_blockhash=recent_blockhash,
        )

        return wire_b58, sig_b58, tip_recipient, valid_tip

    def send_bundle(
        self,
        wire_transactions_b58: List[str],
        simulate_only: bool = False,
    ) -> BundleSubmissionResult:
        """
        Submits wire transaction bundle to Jito Block Engine with multi-region failover.
        """
        bundle_uuid = f"bundle-{int(time.time()*1000)}-{random.randint(1000, 9999)}"
        sig = wire_transactions_b58[0][:16] if wire_transactions_b58 else "simulated"

        if simulate_only:
            return BundleSubmissionResult(
                success=True,
                bundle_id=bundle_uuid,
                region="simulated",
                signature=sig,
                tip_lamports=100_000,
                tip_account=JITO_TIP_ACCOUNTS[0],
                status="SIMULATED_ACCEPTED",
            )

        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "sendBundle",
            "params": [wire_transactions_b58],
        }

        # Try primary region, then fallback
        regions_to_try = [self.primary_region] + [r for r in self._active_regions if r != self.primary_region]

        last_error = None
        for region in regions_to_try:
            url = JITO_REGIONS.get(region, JITO_REGIONS["default"])
            try:
                resp = requests.post(url, json=payload, timeout=self.timeout_sec)
                data = resp.json()
                if "result" in data:
                    returned_id = data["result"]
                    return BundleSubmissionResult(
                        success=True,
                        bundle_id=returned_id or bundle_uuid,
                        region=region,
                        signature=sig,
                        tip_lamports=100_000,
                        tip_account=JITO_TIP_ACCOUNTS[0],
                        status="SUBMITTED",
                    )
                elif "error" in data:
                    last_error = f"RPC error on {region}: {data['error']}"
                    logger.warning(last_error)
            except Exception as e:
                last_error = f"Connection failed on {region}: {e}"
                logger.warning(last_error)
                continue

        return BundleSubmissionResult(
            success=False,
            bundle_id=bundle_uuid,
            region="all_failed",
            signature=sig,
            tip_lamports=0,
            tip_account="",
            status="FAILED",
            error=last_error or "All Jito Block Engine endpoints unreachable",
        )

    def get_bundle_statuses(self, bundle_ids: List[str]) -> Dict[str, Any]:
        """Queries bundle landing status from Jito Block Engine."""
        url = JITO_REGIONS.get(self.primary_region, JITO_REGIONS["default"])
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getBundleStatuses",
            "params": [bundle_ids],
        }
        try:
            resp = requests.post(url, json=payload, timeout=self.timeout_sec)
            return resp.json().get("result", {})
        except Exception as e:
            logger.error(f"Failed to fetch bundle statuses: {e}")
            return {}
