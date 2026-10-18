"""
On-Chain Security & Authority Auditor for Solana Token Mints.
Enforces Invariant 5 & Check 6/7/8 of the 12-Check Gate:
1. Mint authority permanently revoked (mint_authority_option == 0).
2. Freeze authority permanently revoked (freeze_authority_option == 0).
3. Non-bonding-curve Top 10 holder concentration <= 25.0%.
4. Metaplex metadata mutability verification.
"""

from dataclasses import dataclass
import struct
from typing import Any, Dict, List, Optional, Tuple

from src.utils.base58 import b58decode, b58encode
from src.protocol.pumpswap import find_program_address

METAPLEX_PROGRAM_ID_STR = "metaqbxxUerdq28cj1RbAWkYQm3ybzjb6a8bt518x1s"


@dataclass(frozen=True)
class MintAccountState:
    mint_authority_option: int
    mint_authority: Optional[str]
    supply: int
    decimals: int
    is_initialized: bool
    freeze_authority_option: int
    freeze_authority: Optional[str]

    @property
    def is_mint_revoked(self) -> bool:
        return self.mint_authority_option == 0

    @property
    def is_freeze_revoked(self) -> bool:
        return self.freeze_authority_option == 0


@dataclass(frozen=True)
class AuthorityAuditResult:
    passed: bool
    reason: str
    mint_revoked: bool
    freeze_revoked: bool
    top10_concentration_pct: float
    is_metadata_immutable: bool
    mint_address: str


def parse_spl_mint_data(raw_data: bytes) -> MintAccountState:
    """
    Parses canonical 82-byte SPL Token Mint layout:
    [0..4]   mint_authority_option (u32)
    [4..36]  mint_authority (32B)
    [36..44] supply (u64)
    [44..45] decimals (u8)
    [45..46] is_initialized (bool)
    [46..50] freeze_authority_option (u32)
    [50..82] freeze_authority (32B)
    """
    if len(raw_data) < 82:
        raise ValueError(f"SPL mint account data too short: {len(raw_data)} bytes (expected >= 82)")

    mint_auth_opt = struct.unpack_from("<I", raw_data, 0)[0]
    mint_auth = b58encode(raw_data[4:36]) if mint_auth_opt != 0 else None

    supply = struct.unpack_from("<Q", raw_data, 36)[0]
    decimals = raw_data[44]
    is_init = bool(raw_data[45])

    freeze_auth_opt = struct.unpack_from("<I", raw_data, 46)[0]
    freeze_auth = b58encode(raw_data[50:82]) if freeze_auth_opt != 0 else None

    return MintAccountState(
        mint_authority_option=mint_auth_opt,
        mint_authority=mint_auth,
        supply=supply,
        decimals=decimals,
        is_initialized=is_init,
        freeze_authority_option=freeze_auth_opt,
        freeze_authority=freeze_auth,
    )


def derive_metaplex_metadata_pda(mint_address: str) -> str:
    """Derives Metaplex metadata account address for a token mint."""
    mint_bytes = b58decode(mint_address)
    meta_prog_bytes = b58decode(METAPLEX_PROGRAM_ID_STR)
    seeds = [b"metadata", meta_prog_bytes, mint_bytes]
    pda, _ = find_program_address(seeds, METAPLEX_PROGRAM_ID_STR)
    return pda


def parse_metadata_mutability(raw_data: bytes) -> bool:
    """
    Parses the is_mutable flag from Metaplex MetadataV1 binary data.
    Returns True if metadata is mutable, False if immutable.
    """
    if len(raw_data) < 68:
        return True  # Conservatively assume mutable if too short

    offset = 1 + 32 + 32  # key (1) + update_auth (32) + mint (32) = 65
    try:
        # String 1: Name (4B len + str)
        name_len = struct.unpack_from("<I", raw_data, offset)[0]
        offset += 4 + name_len

        # String 2: Symbol (4B len + str)
        symbol_len = struct.unpack_from("<I", raw_data, offset)[0]
        offset += 4 + symbol_len

        # String 3: URI (4B len + str)
        uri_len = struct.unpack_from("<I", raw_data, offset)[0]
        offset += 4 + uri_len

        # Seller fee (2B)
        offset += 2

        # Creators option (1B)
        creators_opt = raw_data[offset]
        offset += 1
        if creators_opt != 0:
            creators_len = struct.unpack_from("<I", raw_data, offset)[0]
            offset += 4 + creators_len * (32 + 1 + 1)  # pubkey + verified + share

        # primary_sale_happened (1B)
        offset += 1

        # is_mutable (1B)
        is_mutable = bool(raw_data[offset])
        return is_mutable
    except Exception:
        return True  # Fallback to mutable on parse failure


class AuthorityAuditor:
    """Validates on-chain security parameters for PumpSwap token candidates."""

    MAX_TOP10_CONCENTRATION_PCT: float = 25.0

    @classmethod
    def audit_mint_data(cls, raw_mint_bytes: bytes) -> Tuple[bool, bool, Optional[MintAccountState]]:
        """Parses mint data and validates mint and freeze authority status."""
        try:
            mint_state = parse_spl_mint_data(raw_mint_bytes)
            return mint_state.is_mint_revoked, mint_state.is_freeze_revoked, mint_state
        except Exception:
            return False, False, None

    @classmethod
    def calculate_top10_concentration(
        cls,
        total_supply: int,
        largest_holders: List[Dict[str, Any]],
        bonding_curve_address: Optional[str] = None,
    ) -> float:
        """
        Calculates percentage of supply held by top 10 retail/insider accounts,
        explicitly excluding the bonding curve reserve account.
        """
        if total_supply <= 0:
            return 100.0

        filtered_holders = []
        for h in largest_holders:
            addr = h.get("address", "")
            amount = int(h.get("amount", 0))
            if bonding_curve_address and addr == bonding_curve_address:
                continue
            filtered_holders.append(amount)

        filtered_holders.sort(reverse=True)
        top10_sum = sum(filtered_holders[:10])
        concentration_pct = (top10_sum / total_supply) * 100.0
        return round(concentration_pct, 2)

    @classmethod
    def audit_candidate(
        cls,
        mint_address: str,
        raw_mint_bytes: bytes,
        largest_holders: List[Dict[str, Any]],
        bonding_curve_address: str,
        raw_metadata_bytes: Optional[bytes] = None,
        require_immutable_metadata: bool = False,
    ) -> AuthorityAuditResult:
        """
        Comprehensive audit verifying:
        1. Mint revoked == True
        2. Freeze revoked == True
        3. Top 10 concentration <= 25.0%
        4. Metadata immutability (optional strict mode)
        """
        mint_revoked, freeze_revoked, mint_state = cls.audit_mint_data(raw_mint_bytes)
        if not mint_revoked:
            return AuthorityAuditResult(
                passed=False,
                reason="Mint authority is active (unrevoked token)",
                mint_revoked=False,
                freeze_revoked=freeze_revoked,
                top10_concentration_pct=100.0,
                is_metadata_immutable=False,
                mint_address=mint_address,
            )

        if not freeze_revoked:
            return AuthorityAuditResult(
                passed=False,
                reason="Freeze authority is active (honeypot risk)",
                mint_revoked=True,
                freeze_revoked=False,
                top10_concentration_pct=100.0,
                is_metadata_immutable=False,
                mint_address=mint_address,
            )

        supply = mint_state.supply if mint_state else 0
        top10_conc = cls.calculate_top10_concentration(supply, largest_holders, bonding_curve_address)
        if top10_conc > cls.MAX_TOP10_CONCENTRATION_PCT:
            return AuthorityAuditResult(
                passed=False,
                reason=f"Top 10 concentration excessive: {top10_conc:.2f}% > {cls.MAX_TOP10_CONCENTRATION_PCT}%",
                mint_revoked=True,
                freeze_revoked=True,
                top10_concentration_pct=top10_conc,
                is_metadata_immutable=False,
                mint_address=mint_address,
            )

        is_immutable = True
        if raw_metadata_bytes:
            is_mutable = parse_metadata_mutability(raw_metadata_bytes)
            is_immutable = not is_mutable
            if require_immutable_metadata and is_mutable:
                return AuthorityAuditResult(
                    passed=False,
                    reason="Token metadata is mutable",
                    mint_revoked=True,
                    freeze_revoked=True,
                    top10_concentration_pct=top10_conc,
                    is_metadata_immutable=False,
                    mint_address=mint_address,
                )

        return AuthorityAuditResult(
            passed=True,
            reason="All authority and security audits passed",
            mint_revoked=True,
            freeze_revoked=True,
            top10_concentration_pct=top10_conc,
            is_metadata_immutable=is_immutable,
            mint_address=mint_address,
        )
