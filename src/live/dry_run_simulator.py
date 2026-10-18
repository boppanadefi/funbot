"""
Zero-Capital Dry-Run Simulator & Dual-Path Verification Engine.
Enforces Phase 10 & Milestone 3 Directives:
1. Pure Simulation Mode: Operates safely with zero live private key requirement and zero real capital at risk.
2. Section 7.1 Bankroll Isolation: $25.00 active limit, $75.00 cold reserve, 0.005 SOL exit reserve.
3. Dual-Path Simulation:
   - Path 1: Helius JSON-RPC simulateTransaction (CU usage, instruction logs, error validation).
   - Path 2: Jito Bundle Structure Validation (tip-after-swap, 1.5% tip cap, canonical tip accounts).
"""

from dataclasses import dataclass, field
from enum import Enum
import base64
import logging
import time
from typing import Any, Dict, List, Optional, Tuple

import requests

from src.protocol.pumpswap import (
    CompiledInstruction,
    PUMPSWAP_PROGRAM_ID_STR,
    TOKEN_PROGRAM_STR,
    SYSTEM_PROGRAM_STR,
    get_associated_token_address,
    build_buy_instruction,
    build_sell_instruction,
)
from src.live.wire_tx_builder import (
    SolanaWireTransactionBuilder,
    BlockhashManager,
    build_set_compute_unit_limit,
    build_set_compute_unit_price,
    build_system_transfer,
)
from src.live.wallet_manager import WalletManager, WalletAccount, b58encode, b58decode
from src.live.jito_client import JITO_TIP_ACCOUNTS, JitoClient, TipCapExceededError, InsufficientReserveError
from src.utils.config import ConfigLoader

logger = logging.getLogger("DryRunSimulator")


class SimulationOutcome(str, Enum):
    SUCCESS = "SUCCESS"
    SIMULATION_ERROR = "SIMULATION_ERROR"
    TIP_CAP_EXCEEDED = "TIP_CAP_EXCEEDED"
    INSUFFICIENT_RESERVE = "INSUFFICIENT_RESERVE"
    RPC_ERROR = "RPC_ERROR"


@dataclass
class SimulatedWalletContext:
    """Represents a simulated wallet context guaranteeing zero real capital risk."""
    pubkey: str
    is_live: bool = False
    balance_sol: float = 0.16129  # Equivalent to $25.00 at $155 SOL
    balance_usd: float = 25.00
    active_limit_usd: float = 25.00
    cold_reserve_usd: float = 75.00
    emergency_reserve_sol: float = 0.005
    label: str = "SIMULATION ZERO-CAPITAL SUB-WALLET"

    def banner(self) -> str:
        return (
            "====================================================================\n"
            "*** SIMULATION MODE ACTIVE — ZERO LIVE CAPITAL AT RISK ***\n"
            f"Active Sub-Wallet (Simulated): {self.balance_sol:.5f} SOL (${self.balance_usd:.2f})\n"
            f"Cold Segregated Reserve (Locked): ${self.cold_reserve_usd:.2f}\n"
            f"Emergency Exit Reserve (Protected): {self.emergency_reserve_sol:.4f} SOL\n"
            "===================================================================="
        )


@dataclass
class DryRunReceipt:
    """Detailed cryptographic and execution receipt for a dry-run transaction."""
    simulation_id: str
    pool_id: str
    mint: str
    side: str
    simulated_pubkey: str
    recent_blockhash: str
    outcome: SimulationOutcome
    units_consumed: int
    simulation_logs: List[str]
    jito_tip_lamports: int
    tip_account: str
    tip_cap_pct: float
    helius_sim_latency_ms: float
    jito_validation_status: str
    error_message: Optional[str] = None
    timestamp: float = field(default_factory=time.time)

    def summary(self) -> Dict[str, Any]:
        return {
            "simulation_id": self.simulation_id,
            "outcome": self.outcome.value,
            "pool_id": self.pool_id,
            "side": self.side,
            "units_consumed": self.units_consumed,
            "jito_tip_lamports": self.jito_tip_lamports,
            "tip_cap_pct": self.tip_cap_pct,
            "helius_sim_latency_ms": round(self.helius_sim_latency_ms, 2),
            "jito_validation": self.jito_validation_status,
            "error": self.error_message,
        }


class DryRunSimulator:
    """
    Executes end-to-end zero-capital dry-run simulation against live Solana Mainnet
    without requiring a real private key or broadcasting transactions.
    """

    DEFAULT_CU_LIMIT = 250_000
    DEFAULT_PRIORITY_FEE_MICRO_LAMPORTS = 1_000
    MIN_EXIT_RESERVE_LAMPORTS = 5_000_000  # 0.005 SOL
    MAX_TIP_PCT_NOTIONAL = 1.50

    def __init__(
        self,
        wallet_mgr: Optional[WalletManager] = None,
        http_rpc_url: Optional[str] = None,
        sol_price_usd: float = 155.0,
    ):
        self.sol_price_usd = sol_price_usd
        self.wallet_mgr = wallet_mgr or WalletManager(sol_price_usd=sol_price_usd)
        
        cfg = ConfigLoader().load_telemetry_config()
        self.http_rpc_url = http_rpc_url or cfg.http_rpc_url
        self.blockhash_mgr = BlockhashManager(rpc_url=self.http_rpc_url)
        self.jito_client = JitoClient()

        # Build simulated context
        self.sim_context = SimulatedWalletContext(
            pubkey=self.wallet_mgr.active_wallet.pubkey,
            is_live=self.wallet_mgr.active_wallet.is_live,
            balance_sol=self.wallet_mgr.active_wallet.balance_sol or 0.16129,
            balance_usd=self.wallet_mgr.active_wallet.balance_usd or 25.00,
        )

    def simulate_solana_transaction(
        self,
        wire_tx_bytes: bytes,
        sig_verify: bool = False,
    ) -> Tuple[bool, int, List[str], Optional[str], float]:
        """
        Submits wire transaction to Helius JSON-RPC simulateTransaction.
        Returns: (success, units_consumed, logs, error_message, latency_ms)
        """
        t0 = time.time()
        tx_b64 = base64.b64encode(wire_tx_bytes).decode("ascii")
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "simulateTransaction",
            "params": [
                tx_b64,
                {
                    "sigVerify": sig_verify,
                    "encoding": "base64",
                    "commitment": "processed",
                },
            ],
        }

        try:
            resp = requests.post(self.http_rpc_url, json=payload, timeout=5.0)
            latency_ms = (time.time() - t0) * 1000.0

            if resp.status_code == 200:
                data = resp.json()
                if "error" in data:
                    err_msg = data["error"].get("message", str(data["error"]))
                    return False, 0, [], f"RPC Error: {err_msg}", latency_ms

                result_val = data.get("result", {}).get("value", {})
                err = result_val.get("err")
                units = result_val.get("unitsConsumed", 0)
                logs = result_val.get("logs", [])

                if err is not None:
                    return False, units, logs, f"Simulation Execution Error: {err}", latency_ms

                return True, units, logs, None, latency_ms
            else:
                return False, 0, [], f"HTTP {resp.status_code}: {resp.text}", latency_ms

        except Exception as e:
            latency_ms = (time.time() - t0) * 1000.0
            logger.warning("Helius simulateTransaction RPC call failed: %s", e)
            return False, 0, [], f"Network Exception: {e}", latency_ms

    def validate_jito_bundle_structure(
        self,
        instructions: List[CompiledInstruction],
        notional_lamports: int,
        tip_lamports: int,
        wallet_balance_lamports: int,
    ) -> Tuple[bool, str, Optional[str]]:
        """
        Validates bundle structure against Invariant 6 without broadcasting:
        1. Swap instruction must appear before tip transfer.
        2. Tip recipient must be one of JITO_TIP_ACCOUNTS.
        3. Tip cap <= 1.5% notional.
        4. Post-trade reserve >= 0.005 SOL.
        """
        # Find swap and tip indices
        swap_idx: Optional[int] = None
        tip_idx: Optional[int] = None
        tip_recipient: Optional[str] = None

        for idx, ix in enumerate(instructions):
            if ix.program_id == PUMPSWAP_PROGRAM_ID_STR:
                swap_idx = idx
            elif ix.program_id == SYSTEM_PROGRAM_STR:
                # System transfer of tip
                if len(ix.accounts) >= 2:
                    recipient = ix.accounts[1].pubkey
                    if recipient in JITO_TIP_ACCOUNTS:
                        tip_idx = idx
                        tip_recipient = recipient

        # Check 1: Instruction ordering
        if swap_idx is None:
            return False, "INVALID_STRUCTURE", "Missing PumpSwap swap instruction."

        if tip_idx is None:
            return False, "INVALID_STRUCTURE", "Missing valid Jito tip instruction targeting canonical tip accounts."

        if tip_idx < swap_idx:
            return False, "ORDERING_VIOLATION", "Invariant 6 Violation: Jito tip instruction MUST follow the swap instruction."

        # Check 2: Tip recipient account validity
        if tip_recipient not in JITO_TIP_ACCOUNTS:
            return False, "INVALID_TIP_RECIPIENT", f"Tip recipient {tip_recipient} is not a canonical Jito tip account."

        # Check 3: 1.5% notional tip cap
        max_allowed_tip = int(notional_lamports * (self.MAX_TIP_PCT_NOTIONAL / 100.0))
        if tip_lamports > max_allowed_tip:
            return (
                False,
                "TIP_CAP_EXCEEDED",
                f"Tip {tip_lamports} lamports exceeds 1.5% notional cap of {max_allowed_tip} lamports.",
            )

        # Check 4: Mandatory 0.005 SOL exit reserve
        remaining = wallet_balance_lamports - notional_lamports - tip_lamports
        if remaining < self.MIN_EXIT_RESERVE_LAMPORTS:
            return (
                False,
                "INSUFFICIENT_RESERVE",
                f"Trade leaves {remaining} lamports, breaching 0.005 SOL exit reserve ({self.MIN_EXIT_RESERVE_LAMPORTS} lamports).",
            )

        return True, "PASSED_INVARIANT_6", None

    def execute_dry_run_swap(
        self,
        pool_id: str,
        mint: str,
        notional_usd: float = 3.00,
        side: str = "BUY",
        tip_lamports: int = 100_000,
    ) -> DryRunReceipt:
        """
        Executes end-to-end zero-capital dry-run swap:
        1. Derives accounts & builds instructions (ComputeBudget, Swap, Jito Tip).
        2. Validates Jito bundle structure.
        3. Retrieves fresh mainnet blockhash from Helius RPC.
        4. Serializes authentic wire transaction.
        5. Simulates via Helius simulateTransaction.
        6. Emits structured DryRunReceipt.
        """
        sim_id = f"dryrun_{int(time.time() * 1000)}"
        user_pubkey = self.sim_context.pubkey
        notional_sol = notional_usd / self.sol_price_usd
        notional_lamports = int(notional_sol * 1e9)
        wallet_bal_lamports = int(self.sim_context.balance_sol * 1e9)

        # Selected canonical tip account
        tip_account = self.jito_client.get_random_tip_account()

        # Build instruction list
        instructions: List[CompiledInstruction] = [
            build_set_compute_unit_limit(self.DEFAULT_CU_LIMIT),
            build_set_compute_unit_price(self.DEFAULT_PRIORITY_FEE_MICRO_LAMPORTS),
        ]

        # Add swap instruction
        if side.upper() == "BUY":
            swap_ix = build_buy_instruction(
                pool_id=pool_id,
                mint=mint,
                user_pubkey=user_pubkey,
                sol_amount_lamports=notional_lamports,
                min_token_output=1,
            )
        else:
            swap_ix = build_sell_instruction(
                pool_id=pool_id,
                mint=mint,
                user_pubkey=user_pubkey,
                token_amount=1_000_000,
                min_sol_output_lamports=notional_lamports,
            )
        instructions.append(swap_ix)

        # Add Jito tip instruction (strictly after swap)
        tip_ix = build_system_transfer(
            sender=user_pubkey,
            recipient=tip_account,
            lamports=tip_lamports,
        )
        instructions.append(tip_ix)

        # 1. Validate Jito bundle structure (Invariant 6)
        valid_jito, jito_status, jito_err = self.validate_jito_bundle_structure(
            instructions=instructions,
            notional_lamports=notional_lamports,
            tip_lamports=tip_lamports,
            wallet_balance_lamports=wallet_bal_lamports,
        )

        tip_cap_pct = (tip_lamports / notional_lamports) * 100.0 if notional_lamports > 0 else 0.0

        if not valid_jito:
            outcome = SimulationOutcome.TIP_CAP_EXCEEDED if "TIP_CAP" in jito_status else SimulationOutcome.SIMULATION_ERROR
            return DryRunReceipt(
                simulation_id=sim_id,
                pool_id=pool_id,
                mint=mint,
                side=side,
                simulated_pubkey=user_pubkey,
                recent_blockhash="N/A",
                outcome=outcome,
                units_consumed=0,
                simulation_logs=[],
                jito_tip_lamports=tip_lamports,
                tip_account=tip_account,
                tip_cap_pct=tip_cap_pct,
                helius_sim_latency_ms=0.0,
                jito_validation_status=jito_status,
                error_message=jito_err,
            )

        # 2. Get fresh blockhash
        recent_bh = self.blockhash_mgr.get_recent_blockhash()

        # 3. Assemble and sign wire transaction (simulated Ed25519 signature)
        wire_tx_bytes, wire_b58, sig_b58 = SolanaWireTransactionBuilder.build_signed_wire_transaction(
            wallet=self.wallet_mgr.active_wallet,
            instructions=instructions,
            recent_blockhash=recent_bh,
        )

        # 4. Simulate transaction on Helius RPC
        sim_ok, units, logs, sim_err, latency_ms = self.simulate_solana_transaction(
            wire_tx_bytes=wire_tx_bytes,
            sig_verify=False,
        )

        outcome = SimulationOutcome.SUCCESS if sim_ok else SimulationOutcome.SIMULATION_ERROR
        if sim_err and "RPC" in sim_err:
            outcome = SimulationOutcome.RPC_ERROR

        return DryRunReceipt(
            simulation_id=sim_id,
            pool_id=pool_id,
            mint=mint,
            side=side,
            simulated_pubkey=user_pubkey,
            recent_blockhash=recent_bh,
            outcome=outcome,
            units_consumed=units,
            simulation_logs=logs,
            jito_tip_lamports=tip_lamports,
            tip_account=tip_account,
            tip_cap_pct=round(tip_cap_pct, 3),
            helius_sim_latency_ms=latency_ms,
            jito_validation_status=jito_status,
            error_message=sim_err,
        )
