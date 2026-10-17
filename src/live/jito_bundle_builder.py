"""
Atomic Jito Bundle Builder — Guardrail 6.
Enforces atomic transaction structure:
1. Strict instruction ordering: Swap instruction precedes Tip instruction.
2. 1.5% notional tip cap.
3. Dedicated 0.005 SOL emergency exit budget guarantee.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import logging
from typing import Dict, List, Optional

logger = logging.getLogger("JitoBundleBuilder")


class TipCapExceededError(Exception):
    """Raised when Jito tip exceeds the 1.5% notional cap."""
    pass


class InvalidInstructionOrderError(Exception):
    """Raised when bundle instructions violate the required Swap-then-Tip order."""
    pass


class InsufficientExitBudgetError(Exception):
    """Raised when wallet free balance does not maintain the mandatory 0.005 SOL emergency exit reserve."""
    pass


@dataclass
class BundleInstruction:
    program_name: str
    instruction_type: str  # e.g., 'SET_COMPUTE_BUDGET', 'CREATE_ATA', 'PUMPSWAP_SWAP', 'JITO_TIP'
    accounts: List[str]
    data_hex: str
    order_index: int


@dataclass
class AtomicJitoBundle:
    bundle_id: str
    pool_address: str
    token_mint: str
    is_buy: bool
    position_notional_sol: float
    tip_sol: float
    tip_pct_notional: float
    instructions: List[BundleInstruction]
    tip_recipient_pubkey: str
    emergency_exit_budget_sol: float = 0.005
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> Dict:
        return {
            "bundle_id": self.bundle_id,
            "pool_address": self.pool_address,
            "token_mint": self.token_mint,
            "is_buy": self.is_buy,
            "position_notional_sol": self.position_notional_sol,
            "tip_sol": self.tip_sol,
            "tip_pct_notional": self.tip_pct_notional,
            "tip_recipient_pubkey": self.tip_recipient_pubkey,
            "emergency_exit_budget_sol": self.emergency_exit_budget_sol,
            "created_at": self.created_at,
            "instructions": [
                {
                    "order_index": ix.order_index,
                    "program": ix.program_name,
                    "type": ix.instruction_type,
                    "accounts_count": len(ix.accounts),
                }
                for ix in self.instructions
            ],
        }


class JitoBundleBuilder:
    """
    Constructs MEV-protected atomic transaction bundles for PumpSwap executions.
    Strictly enforces Guardrail 6 invariants.
    """

    MAX_TIP_PCT: float = 1.50  # 1.5% notional tip cap
    EMERGENCY_EXIT_RESERVE_SOL: float = 0.005  # Mandatory 0.005 SOL reserve

    # Recognized canonical Jito tip accounts
    JITO_TIP_ACCOUNTS = [
        "96gYZGLnJYVFmbjzopPSU6QiEV5fGqZNyN9nmNhvrZU5",
        "HFqU5x63VTqvQss8hp11i4wVV8bD44PvwucfZ2bU7gRe",
        "Cw8CFyM9FkoMi7K7Crf6HNQqf4uEMzpKw6QNghXLvLkY",
        "ADaUMid9yfUytqMBgopwjb2DTLSokTSzL1zt6iGPaS49",
        "DfXygSm4jCyNCybVYYK6DwvWqjKee8pbDmJGcLWNDXjh",
    ]

    def __init__(self, sol_price_usd: float = 155.0):
        self.sol_price_usd = sol_price_usd

    def calculate_max_tip_sol(self, position_notional_sol: float) -> float:
        """Returns the absolute maximum allowable Jito tip under the 1.5% notional cap."""
        return round(position_notional_sol * (self.MAX_TIP_PCT / 100.0), 6)

    def validate_exit_budget(self, wallet_free_sol: float, trade_sol: float) -> bool:
        """Verifies wallet retains at least 0.005 SOL dedicated emergency exit budget."""
        remaining = wallet_free_sol - trade_sol
        if remaining < self.EMERGENCY_EXIT_RESERVE_SOL:
            raise InsufficientExitBudgetError(
                f"Trade would leave {remaining:.5f} SOL free, violating the mandatory "
                f"{self.EMERGENCY_EXIT_RESERVE_SOL:.4f} SOL emergency exit reserve."
            )
        return True

    def build_atomic_swap_bundle(
        self,
        pool_address: str,
        token_mint: str,
        is_buy: bool,
        position_notional_sol: float,
        proposed_tip_sol: float,
        user_wallet_pubkey: str,
        tip_account_index: int = 0,
    ) -> AtomicJitoBundle:
        """
        Builds and validates an atomic transaction bundle.
        Guarantees that:
        1. Tip does not exceed 1.5% notional cap.
        2. Swap instruction is placed BEFORE Tip instruction.
        """
        # 1. Validate Tip Cap
        max_allowed_tip = self.calculate_max_tip_sol(position_notional_sol)
        tip_pct = (proposed_tip_sol / position_notional_sol) * 100.0 if position_notional_sol > 0 else 0.0

        if tip_pct > self.MAX_TIP_PCT + 1e-6:
            raise TipCapExceededError(
                f"Proposed tip of {proposed_tip_sol:.5f} SOL ({tip_pct:.2f}%) exceeds "
                f"Guardrail 6 cap of {self.MAX_TIP_PCT}% ({max_allowed_tip:.5f} SOL)."
            )

        tip_recipient = self.JITO_TIP_ACCOUNTS[tip_account_index % len(self.JITO_TIP_ACCOUNTS)]

        # 2. Build Ordered Instruction List
        # Ordering rule: Compute Budget -> [Create ATA if Buy] -> Swap -> Jito Tip
        instructions: List[BundleInstruction] = []
        idx = 0

        # Ix 0: Compute Budget Priority Fee
        instructions.append(
            BundleInstruction(
                program_name="ComputeBudget111111111111111111111111111111",
                instruction_type="SET_COMPUTE_UNIT_LIMIT_AND_PRICE",
                accounts=[user_wallet_pubkey],
                data_hex="02c05a030000000000",
                order_index=idx,
            )
        )
        idx += 1

        if is_buy:
            # Ix 1: Idempotent ATA creation
            instructions.append(
                BundleInstruction(
                    program_name="AssociatedTokenAccount111111111111111111",
                    instruction_type="CREATE_IDEMPOTENT_ATA",
                    accounts=[user_wallet_pubkey, token_mint],
                    data_hex="01",
                    order_index=idx,
                )
            )
            idx += 1

        # Ix 2: Swap Instruction (CRITICAL: MUST OCCUR BEFORE TIP)
        swap_type = "PUMPSWAP_BUY" if is_buy else "PUMPSWAP_SELL"
        swap_idx = idx
        instructions.append(
            BundleInstruction(
                program_name="PumpSwap66666666666666666666666666666666666",
                instruction_type=swap_type,
                accounts=[pool_address, token_mint, user_wallet_pubkey],
                data_hex=f"08{int(position_notional_sol * 1e9):016x}",
                order_index=swap_idx,
            )
        )
        idx += 1

        # Ix 3: Jito Tip Instruction (STRICTLY PLACED AFTER SWAP)
        tip_idx = idx
        instructions.append(
            BundleInstruction(
                program_name="11111111111111111111111111111111",  # System Program Transfer
                instruction_type="JITO_TIP_TRANSFER",
                accounts=[user_wallet_pubkey, tip_recipient],
                data_hex=f"02000000{int(proposed_tip_sol * 1e9):016x}",
                order_index=tip_idx,
            )
        )

        # 3. Verify Instruction Invariant: Tip strictly after swap
        self._verify_instruction_ordering(instructions)

        bundle_hash = hashlib.sha256(
            f"{pool_address}:{token_mint}:{position_notional_sol}:{proposed_tip_sol}:{datetime.now(timezone.utc).isoformat()}".encode("utf-8")
        ).hexdigest()[:16]

        bundle = AtomicJitoBundle(
            bundle_id=f"bundle_{bundle_hash}",
            pool_address=pool_address,
            token_mint=token_mint,
            is_buy=is_buy,
            position_notional_sol=round(position_notional_sol, 5),
            tip_sol=round(proposed_tip_sol, 6),
            tip_pct_notional=round(tip_pct, 2),
            instructions=instructions,
            tip_recipient_pubkey=tip_recipient,
            emergency_exit_budget_sol=self.EMERGENCY_EXIT_RESERVE_SOL,
        )

        logger.info(
            "Constructed Jito Bundle %s | Tip: %.5f SOL (%.2f%%) | Swap Index: %d, Tip Index: %d",
            bundle.bundle_id, bundle.tip_sol, bundle.tip_pct_notional, swap_idx, tip_idx
        )
        return bundle

    def _verify_instruction_ordering(self, instructions: List[BundleInstruction]) -> None:
        """Enforces that Swap instruction appears before Jito Tip instruction."""
        swap_indices = [ix.order_index for ix in instructions if "SWAP" in ix.instruction_type]
        tip_indices = [ix.order_index for ix in instructions if "TIP" in ix.instruction_type]

        if not swap_indices:
            raise InvalidInstructionOrderError("Bundle must contain a swap instruction.")
        if not tip_indices:
            raise InvalidInstructionOrderError("Bundle must contain a Jito tip instruction.")

        last_swap_index = max(swap_indices)
        first_tip_index = min(tip_indices)

        if first_tip_index <= last_swap_index:
            raise InvalidInstructionOrderError(
                f"CRITICAL SAFETY VIOLATION: Jito tip instruction at index {first_tip_index} "
                f"occurs before or at swap instruction at index {last_swap_index}. "
                f"Tip instruction MUST be placed AFTER swap instruction."
            )
