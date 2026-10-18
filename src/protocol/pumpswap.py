"""
PumpSwap Protocol Interface — Binary Encoders, Account Deserializers & PDA Derivation.
Canonical Program ID: 6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P

Provides zero-dependency Anchor instruction builders, PDA derivation,
and bonding curve account layout deserialization for canonical PumpSwap pools.
"""

from dataclasses import dataclass
import hashlib
import struct
from typing import List, Optional, Tuple

from src.utils.base58 import b58decode, b58encode

# --- Canonical Protocol Constants ---
PUMPSWAP_PROGRAM_ID_STR = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
FEE_RECIPIENT_STR = "CebN5WGQ4jvEPvsVU4EoHEpgzq1VV7AbicfhtW4xC9iM"
SYSTEM_PROGRAM_STR = "11111111111111111111111111111111"
TOKEN_PROGRAM_STR = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
ASSOCIATED_TOKEN_PROGRAM_STR = "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL"
RENT_SYSVAR_STR = "SysvarRent111111111111111111111111111111111"

# Anchor 8-Byte Instruction & Account Discriminators (SHA-256 prefixes)
# sha256("global:buy")[:8] = 66063d1201daebea
BUY_DISCRIMINATOR = bytes([0x66, 0x06, 0x3D, 0x12, 0x01, 0xDA, 0xEB, 0xEA])
# sha256("global:sell")[:8] = 33e685a4017f83ad
SELL_DISCRIMINATOR = bytes([0x33, 0xE6, 0x85, 0xA4, 0x01, 0x7F, 0x83, 0xAD])
# sha256("account:BondingCurve")[:8] = 17b7f83760d8ac60
BONDING_CURVE_DISCRIMINATOR = bytes([0x17, 0xB7, 0xF8, 0x37, 0x60, 0xD8, 0xAC, 0x60])

# Ed25519 Curve constants for PDA validation
_Q = 2**255 - 19
_D = -121665 * pow(121666, _Q - 2, _Q) % _Q
_I = pow(2, (_Q - 1) // 4, _Q)
PDA_MARKER = b"ProgramDerivedAddress"


def is_on_curve(point_bytes: bytes) -> bool:
    """Checks whether 32-byte array represents a valid Ed25519 point."""
    if len(point_bytes) != 32:
        return False
    y = int.from_bytes(point_bytes, "little")
    sign = (y >> 255) & 1
    y &= (1 << 255) - 1
    if y >= _Q:
        return False
    xx = (y * y - 1) * pow(_D * y * y + 1, _Q - 2, _Q) % _Q
    if xx == 0:
        return sign == 0
    x = pow(xx, (_Q + 3) // 8, _Q)
    if (x * x - xx) % _Q != 0:
        x = (x * _I) % _Q
    if (x * x - xx) % _Q != 0:
        return False
    if (x & 1) != sign:
        x = _Q - x
    return (x * x - xx) % _Q == 0


def create_program_address(seeds: List[bytes], program_id_bytes: bytes) -> bytes:
    """Creates a Program Derived Address (PDA) bytes, failing if on curve."""
    data = b"".join(seeds) + program_id_bytes + PDA_MARKER
    digest = hashlib.sha256(data).digest()
    if is_on_curve(digest):
        raise ValueError("Invalid seeds: derived address falls on Ed25519 curve")
    return digest


def find_program_address(seeds: List[bytes], program_id_str: str) -> Tuple[str, int]:
    """Finds a valid PDA and its bump seed (255 down to 0)."""
    prog_bytes = b58decode(program_id_str)
    for bump in range(255, -1, -1):
        try:
            pda_bytes = create_program_address(seeds + [bytes([bump])], prog_bytes)
            return b58encode(pda_bytes), bump
        except ValueError:
            continue
    raise RuntimeError(f"Could not find valid PDA bump for seeds {seeds}")


def get_associated_token_address(wallet_address: str, mint_address: str) -> str:
    """Derives standard SPL Associated Token Address (ATA)."""
    wallet_bytes = b58decode(wallet_address)
    token_prog_bytes = b58decode(TOKEN_PROGRAM_STR)
    mint_bytes = b58decode(mint_address)
    ata, _ = find_program_address(
        [wallet_bytes, token_prog_bytes, mint_bytes],
        ASSOCIATED_TOKEN_PROGRAM_STR,
    )
    return ata


@dataclass(frozen=True)
class AccountMeta:
    pubkey: str
    is_signer: bool
    is_writable: bool


@dataclass(frozen=True)
class CompiledInstruction:
    program_id: str
    accounts: List[AccountMeta]
    data: bytes


@dataclass(frozen=True)
class BondingCurveState:
    virtual_token_reserves: int
    virtual_sol_reserves: int
    real_token_reserves: int
    real_sol_reserves: int
    token_total_supply: int
    complete: bool

    @property
    def spot_price_sol(self) -> float:
        """Spot price in SOL per token."""
        if self.virtual_token_reserves <= 0:
            return 0.0
        # 1 SOL = 10^9 lamports. If token has 6 decimals:
        # Price (SOL/token) = (virtual_sol / 1e9) / (virtual_token / 1e6) = (virtual_sol / virtual_token) * 1e-3
        return (self.virtual_sol_reserves / self.virtual_token_reserves) * 1e-3

    @property
    def market_cap_sol(self) -> float:
        """Implied market cap in SOL."""
        return self.spot_price_sol * (self.token_total_supply / 1e6)


def parse_bonding_curve_data(raw_data: bytes) -> BondingCurveState:
    """Deserializes raw binary bonding curve account data (49 bytes)."""
    if len(raw_data) < 49:
        raise ValueError(f"Bonding curve account data too short: {len(raw_data)} bytes (expected >= 49)")

    discriminator = raw_data[:8]
    if discriminator != BONDING_CURVE_DISCRIMINATOR:
        raise ValueError(
            f"Invalid bonding curve discriminator: {discriminator.hex()} "
            f"(expected {BONDING_CURVE_DISCRIMINATOR.hex()})"
        )

    # 5x u64 little-endian + 1x bool
    v_token, v_sol, r_token, r_sol, total_supply, complete = struct.unpack_from("<QQQQQ?", raw_data, 8)
    return BondingCurveState(
        virtual_token_reserves=v_token,
        virtual_sol_reserves=v_sol,
        real_token_reserves=r_token,
        real_sol_reserves=r_sol,
        token_total_supply=total_supply,
        complete=complete,
    )


class PumpSwapProgram:
    """Canonical PumpSwap Program Interface & Instruction Factory."""

    def __init__(self, program_id: str = PUMPSWAP_PROGRAM_ID_STR, fee_recipient: str = FEE_RECIPIENT_STR):
        self.program_id = program_id
        self.fee_recipient = fee_recipient
        self._global_pda, self._global_bump = find_program_address([b"global"], self.program_id)
        self._event_auth, self._event_bump = find_program_address([b"__event_authority"], self.program_id)

    @property
    def global_pda(self) -> str:
        return self._global_pda

    @property
    def event_authority(self) -> str:
        return self._event_auth

    def get_bonding_curve_pda(self, mint_address: str) -> Tuple[str, int]:
        """Derives bonding curve PDA for a token mint."""
        mint_bytes = b58decode(mint_address)
        return find_program_address([b"bonding-curve", mint_bytes], self.program_id)

    def get_bonding_curve_ata(self, bonding_curve_address: str, mint_address: str) -> str:
        """Derives associated token account for the bonding curve."""
        return get_associated_token_address(bonding_curve_address, mint_address)

    def build_buy_instruction(
        self,
        user_address: str,
        mint_address: str,
        amount_tokens: int,
        max_sol_cost_lamports: int,
    ) -> CompiledInstruction:
        """
        Builds canonical PumpSwap Buy instruction.
        Data layout: [8B discriminator][8B u64 amount_tokens][8B u64 max_sol_cost]
        """
        if amount_tokens <= 0:
            raise ValueError(f"Amount tokens must be > 0, got {amount_tokens}")
        if max_sol_cost_lamports <= 0:
            raise ValueError(f"Max SOL cost must be > 0, got {max_sol_cost_lamports}")

        bonding_curve_pda, _ = self.get_bonding_curve_pda(mint_address)
        bonding_curve_ata = self.get_bonding_curve_ata(bonding_curve_pda, mint_address)
        user_ata = get_associated_token_address(user_address, mint_address)

        # 12 Accounts in exact Anchor IDL order
        accounts = [
            AccountMeta(self.global_pda, is_signer=False, is_writable=False),
            AccountMeta(self.fee_recipient, is_signer=False, is_writable=True),
            AccountMeta(mint_address, is_signer=False, is_writable=False),
            AccountMeta(bonding_curve_pda, is_signer=False, is_writable=True),
            AccountMeta(bonding_curve_ata, is_signer=False, is_writable=True),
            AccountMeta(user_ata, is_signer=False, is_writable=True),
            AccountMeta(user_address, is_signer=True, is_writable=True),
            AccountMeta(SYSTEM_PROGRAM_STR, is_signer=False, is_writable=False),
            AccountMeta(TOKEN_PROGRAM_STR, is_signer=False, is_writable=False),
            AccountMeta(RENT_SYSVAR_STR, is_signer=False, is_writable=False),
            AccountMeta(self.event_authority, is_signer=False, is_writable=False),
            AccountMeta(self.program_id, is_signer=False, is_writable=False),
        ]

        data = BUY_DISCRIMINATOR + struct.pack("<QQ", amount_tokens, max_sol_cost_lamports)
        return CompiledInstruction(program_id=self.program_id, accounts=accounts, data=data)

    def build_sell_instruction(
        self,
        user_address: str,
        mint_address: str,
        amount_tokens: int,
        min_sol_output_lamports: int,
    ) -> CompiledInstruction:
        """
        Builds canonical PumpSwap Sell instruction.
        Data layout: [8B discriminator][8B u64 amount_tokens][8B u64 min_sol_output]
        """
        if amount_tokens <= 0:
            raise ValueError(f"Amount tokens must be > 0, got {amount_tokens}")
        if min_sol_output_lamports < 0:
            raise ValueError(f"Min SOL output must be >= 0, got {min_sol_output_lamports}")

        bonding_curve_pda, _ = self.get_bonding_curve_pda(mint_address)
        bonding_curve_ata = self.get_bonding_curve_ata(bonding_curve_pda, mint_address)
        user_ata = get_associated_token_address(user_address, mint_address)

        # 12 Accounts in exact Anchor IDL order
        accounts = [
            AccountMeta(self.global_pda, is_signer=False, is_writable=False),
            AccountMeta(self.fee_recipient, is_signer=False, is_writable=True),
            AccountMeta(mint_address, is_signer=False, is_writable=False),
            AccountMeta(bonding_curve_pda, is_signer=False, is_writable=True),
            AccountMeta(bonding_curve_ata, is_signer=False, is_writable=True),
            AccountMeta(user_ata, is_signer=False, is_writable=True),
            AccountMeta(user_address, is_signer=True, is_writable=True),
            AccountMeta(SYSTEM_PROGRAM_STR, is_signer=False, is_writable=False),
            AccountMeta(ASSOCIATED_TOKEN_PROGRAM_STR, is_signer=False, is_writable=False),
            AccountMeta(TOKEN_PROGRAM_STR, is_signer=False, is_writable=False),
            AccountMeta(self.event_authority, is_signer=False, is_writable=False),
            AccountMeta(self.program_id, is_signer=False, is_writable=False),
        ]

        data = SELL_DISCRIMINATOR + struct.pack("<QQ", amount_tokens, min_sol_output_lamports)
        return CompiledInstruction(program_id=self.program_id, accounts=accounts, data=data)


# Module-level convenience wrappers
_default_pumpswap = PumpSwapProgram()


def build_buy_instruction(
    pool_id: str,
    mint: str,
    user_pubkey: str,
    sol_amount_lamports: int,
    min_token_output: int = 1,
) -> CompiledInstruction:
    """Builds canonical PumpSwap buy instruction using the default program instance."""
    return _default_pumpswap.build_buy_instruction(
        user_address=user_pubkey,
        mint_address=mint,
        amount_tokens=min_token_output,
        max_sol_cost_lamports=sol_amount_lamports,
    )


def build_sell_instruction(
    pool_id: str,
    mint: str,
    user_pubkey: str,
    token_amount: int,
    min_sol_output_lamports: int = 0,
) -> CompiledInstruction:
    """Builds canonical PumpSwap sell instruction using the default program instance."""
    return _default_pumpswap.build_sell_instruction(
        user_address=user_pubkey,
        mint_address=mint,
        amount_tokens=token_amount,
        min_sol_output_lamports=min_sol_output_lamports,
    )
