"""
Token Mint & Freeze Authority Safety Checks.
Enforces Guardrail 7: Audit mint and freeze authorities before opening positions.
"""

from typing import Dict, List, Optional
from src.cluster.models import SafetyCheckResult
from src.telemetry.pool_discovery import NATIVE_SOL_MINT


class TokenSafetyAuditor:
    """
    Audits token metadata and SPL token accounts for security traps (honeypots, unrevoked freeze/mint).
    """

    def audit_token(
        self,
        token_mint: str,
        mint_authority: Optional[str] = None,
        freeze_authority: Optional[str] = None,
        quote_mint: str = NATIVE_SOL_MINT,
    ) -> SafetyCheckResult:
        """
        Audits SPL token authorities.
        For a canonical safe Pump.fun token:
        1. Mint authority must be None (no additional token inflation).
        2. Freeze authority must be None (no blacklisting or honeypot freezes).
        3. Quote asset must be canonical SOL (no USDC/fake quote pairs).
        """
        failure_reasons: List[str] = []

        mint_revoked = mint_authority is None or mint_authority.strip() in ("", "None", "11111111111111111111111111111111")
        if not mint_revoked:
            failure_reasons.append(f"Mint authority active: {mint_authority}")

        freeze_revoked = freeze_authority is None or freeze_authority.strip() in ("", "None", "11111111111111111111111111111111")
        if not freeze_revoked:
            failure_reasons.append(f"Freeze authority active (honeypot risk): {freeze_authority}")

        is_canonical_sol = (quote_mint == NATIVE_SOL_MINT)
        if not is_canonical_sol:
            failure_reasons.append(f"Non-canonical quote pair (expected SOL, got {quote_mint})")

        is_safe = (mint_revoked and freeze_revoked and is_canonical_sol)

        return SafetyCheckResult(
            token_mint=token_mint,
            mint_authority_revoked=mint_revoked,
            freeze_authority_revoked=freeze_revoked,
            is_canonical_sol=is_canonical_sol,
            is_safe=is_safe,
            failure_reasons=failure_reasons,
        )
