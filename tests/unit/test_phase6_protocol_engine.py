"""
Unit Test Suite for Phase 6: Protocol & AMM Engine.
Covers:
1. PumpSwap program binary encoders, PDA derivation, and bonding curve deserializer (todo-01)
2. Constant-product AMM math, price impact, and sell quote simulation (todo-08)
3. On-chain authority auditor, SPL mint parsing, and concentration checks (todo-07)
"""

import struct
import pytest

from src.protocol.pumpswap import (
    PUMPSWAP_PROGRAM_ID_STR,
    FEE_RECIPIENT_STR,
    BUY_DISCRIMINATOR,
    SELL_DISCRIMINATOR,
    BONDING_CURVE_DISCRIMINATOR,
    BondingCurveState,
    PumpSwapProgram,
    find_program_address,
    get_associated_token_address,
    parse_bonding_curve_data,
    is_on_curve,
)
from src.amm.quote_calculator import (
    AMMQuoteCalculator,
    BuyQuote,
    SellQuote,
)
from src.security.authority_auditor import (
    AuthorityAuditor,
    AuthorityAuditResult,
    MintAccountState,
    parse_spl_mint_data,
    parse_metadata_mutability,
    derive_metaplex_metadata_pda,
)
from src.live.wallet_manager import b58encode, b58decode


# =====================================================================
# 1. PumpSwap Program Interface & Binary Layout Tests (todo-01)
# =====================================================================

class TestPumpSwapProgramInterface:
    def setup_method(self):
        self.program = PumpSwapProgram()
        self.dummy_mint = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"
        self.dummy_user = "71kM3Wc7hS4nFzYt8Q2n5K1p9M6a4v8e3r1t7y9u5i2o"

    def test_anchor_discriminator_values(self):
        """Verify 8-byte Anchor discriminators match protocol definitions."""
        assert BUY_DISCRIMINATOR.hex() == "66063d1201daebea"
        assert SELL_DISCRIMINATOR.hex() == "33e685a4017f83ad"
        assert BONDING_CURVE_DISCRIMINATOR.hex() == "17b7f83760d8ac60"

    def test_pda_derivation_deterministic(self):
        """Verify PDA derivation succeeds and returns valid off-curve addresses."""
        global_pda, bump = find_program_address([b"global"], PUMPSWAP_PROGRAM_ID_STR)
        assert isinstance(global_pda, str)
        assert 0 <= bump <= 255
        # PDA must not be on the curve
        assert not is_on_curve(b58decode(global_pda))

    def test_bonding_curve_pda_derivation(self):
        """Verify bonding curve PDA derivation for a given token mint."""
        curve_pda, bump = self.program.get_bonding_curve_pda(self.dummy_mint)
        assert len(curve_pda) > 30
        assert not is_on_curve(b58decode(curve_pda))

        # ATA of bonding curve
        curve_ata = self.program.get_bonding_curve_ata(curve_pda, self.dummy_mint)
        assert len(curve_ata) > 30
        assert not is_on_curve(b58decode(curve_ata))

    def test_build_buy_instruction_structure(self):
        """Verify Buy instruction creates 12 accounts and 24-byte serialized data."""
        tokens_to_buy = 50_000_000_000  # 50k tokens (6 decimals)
        max_sol = 500_000_000  # 0.50 SOL

        ix = self.program.build_buy_instruction(
            user_address=self.dummy_user,
            mint_address=self.dummy_mint,
            amount_tokens=tokens_to_buy,
            max_sol_cost_lamports=max_sol,
        )

        assert ix.program_id == PUMPSWAP_PROGRAM_ID_STR
        assert len(ix.accounts) == 12
        assert len(ix.data) == 24

        # Check account flags
        # User account is account index 6, must be signer and writable
        user_meta = ix.accounts[6]
        assert user_meta.pubkey == self.dummy_user
        assert user_meta.is_signer is True
        assert user_meta.is_writable is True

        # Fee recipient is account index 1, writable
        assert ix.accounts[1].pubkey == FEE_RECIPIENT_STR
        assert ix.accounts[1].is_writable is True

        # Unpack instruction data
        disc = ix.data[:8]
        amount, cost = struct.unpack_from("<QQ", ix.data, 8)
        assert disc == BUY_DISCRIMINATOR
        assert amount == tokens_to_buy
        assert cost == max_sol

    def test_build_sell_instruction_structure(self):
        """Verify Sell instruction creates 12 accounts and 24-byte serialized data."""
        tokens_to_sell = 50_000_000_000
        min_sol = 400_000_000  # 0.40 SOL

        ix = self.program.build_sell_instruction(
            user_address=self.dummy_user,
            mint_address=self.dummy_mint,
            amount_tokens=tokens_to_sell,
            min_sol_output_lamports=min_sol,
        )

        assert ix.program_id == PUMPSWAP_PROGRAM_ID_STR
        assert len(ix.accounts) == 12
        assert len(ix.data) == 24

        # Unpack instruction data
        disc = ix.data[:8]
        amount, min_out = struct.unpack_from("<QQ", ix.data, 8)
        assert disc == SELL_DISCRIMINATOR
        assert amount == tokens_to_sell
        assert min_out == min_sol

    def test_parse_bonding_curve_data(self):
        """Verify deserialization of raw 49-byte bonding curve account."""
        v_token = 1_073_000_000_000_000
        v_sol = 30_000_000_000
        r_token = 793_100_000_000_000
        r_sol = 2_000_000_000
        supply = 1_000_000_000_000_000
        complete = False

        data = BONDING_CURVE_DISCRIMINATOR + struct.pack(
            "<QQQQQ?",
            v_token,
            v_sol,
            r_token,
            r_sol,
            supply,
            complete,
        )

        state = parse_bonding_curve_data(data)
        assert state.virtual_token_reserves == v_token
        assert state.virtual_sol_reserves == v_sol
        assert state.real_token_reserves == r_token
        assert state.real_sol_reserves == r_sol
        assert state.token_total_supply == supply
        assert state.complete is False
        assert state.spot_price_sol > 0

    def test_parse_bonding_curve_data_invalid_discriminator(self):
        """Verify parser rejects corrupt or wrong account discriminator."""
        bad_data = b"X" * 8 + b"\x00" * 41
        with pytest.raises(ValueError, match="Invalid bonding curve discriminator"):
            parse_bonding_curve_data(bad_data)


# =====================================================================
# 2. AMM Math & Sell Quote Simulation Tests (todo-08)
# =====================================================================

class TestAMMQuoteCalculator:
    def setup_method(self):
        # Typical PumpSwap bonding curve reserves
        self.v_sol = 32_000_000_000  # 32 SOL
        self.v_token = 1_000_000_000_000_000  # 1B tokens (6 dec)
        self.r_sol = 2_000_000_000
        self.r_token = 750_000_000_000_000

    def test_spot_price_calculation(self):
        """Verify spot price calculation matches ratio."""
        spot_p = AMMQuoteCalculator.calculate_spot_price_lamports(self.v_sol, self.v_token)
        expected = self.v_sol / self.v_token
        assert spot_p == expected

    def test_buy_quote_constant_product(self):
        """Verify buy quote enforces 1% protocol fee and k = x * y invariant."""
        sol_in = 1_000_000_000  # 1.0 SOL
        quote = AMMQuoteCalculator.get_buy_quote(
            virtual_sol=self.v_sol,
            virtual_token=self.v_token,
            sol_in_lamports=sol_in,
            slippage_bps=200,
        )

        assert quote.sol_in_lamports == sol_in
        assert quote.fee_lamports == 10_000_000  # 1% of 1 SOL = 0.01 SOL
        assert quote.net_sol_effective == 990_000_000

        # Invariant check: (x + dx_eff) * (y - dy) >= x * y
        k_before = self.v_sol * self.v_token
        k_after = (self.v_sol + quote.net_sol_effective) * (self.v_token - quote.expected_tokens_out)
        assert k_after >= k_before

        assert quote.price_impact_pct > 0.0
        assert quote.max_sol_cost_lamports == sol_in + 20_000_000  # +2.0%

    def test_sell_quote_constant_product(self):
        """Verify sell quote calculates gross and net SOL after 1% fee."""
        tokens_in = 10_000_000_000  # 10k tokens
        quote = AMMQuoteCalculator.get_sell_quote(
            virtual_sol=self.v_sol,
            virtual_token=self.v_token,
            tokens_in=tokens_in,
            slippage_bps=200,
        )

        assert quote.tokens_in == tokens_in
        assert quote.gross_sol_lamports > 0
        assert quote.fee_lamports == (quote.gross_sol_lamports * 100) // 10000
        assert quote.net_sol_lamports == quote.gross_sol_lamports - quote.fee_lamports
        assert quote.min_sol_output_lamports < quote.net_sol_lamports

        # Invariant check: (x - dx_gross) * (y + dy) >= x * y
        k_before = self.v_sol * self.v_token
        k_after = (self.v_sol - quote.gross_sol_lamports) * (self.v_token + tokens_in)
        assert k_after >= k_before

    def test_round_trip_friction(self):
        """Verify buying and immediately selling yields predictable protocol + impact friction."""
        sol_in = 500_000_000  # 0.5 SOL
        buy_q = AMMQuoteCalculator.get_buy_quote(self.v_sol, self.v_token, sol_in)

        # Update reserves after buy
        v_sol_after = self.v_sol + buy_q.net_sol_effective
        v_token_after = self.v_token - buy_q.expected_tokens_out

        # Sell the received tokens back
        sell_q = AMMQuoteCalculator.get_sell_quote(v_sol_after, v_token_after, buy_q.expected_tokens_out)

        # Net round-trip SOL returned must reflect 2x protocol fee (1% buy + 1% sell)
        net_returned = sell_q.net_sol_lamports
        assert net_returned < sol_in
        total_friction_pct = ((sol_in - net_returned) / sol_in) * 100.0
        # Friction must be around ~2.0% + minor rounding
        assert 1.95 <= total_friction_pct <= 2.10

    def test_simulate_path_a_exit(self):
        """Verify Path A+ exit validation under normal and oversized position conditions."""
        curve = BondingCurveState(
            virtual_token_reserves=self.v_token,
            virtual_sol_reserves=self.v_sol,
            real_token_reserves=self.r_token,
            real_sol_reserves=self.r_sol,
            token_total_supply=1_000_000_000_000_000,
            complete=False,
        )

        # Normal small size: $25 worth of tokens (~0.16 SOL -> ~5M tokens)
        small_balance = 5_000_000_000
        passed, quote, reason = AMMQuoteCalculator.simulate_path_a_exit(curve, small_balance)
        assert passed is True
        assert quote.price_impact_pct <= 2.0

        # Massive size: 50% of virtual tokens -> should exceed 2% price impact
        huge_balance = 100_000_000_000_000
        passed, quote, reason = AMMQuoteCalculator.simulate_path_a_exit(curve, huge_balance)
        assert passed is False
        assert "price impact excessive" in reason

    def test_check_dynamic_liquidity_floor(self):
        """Verify dynamic liquidity floor requires <= 1.5% impact on 0.02 SOL test swap."""
        curve = BondingCurveState(
            virtual_token_reserves=self.v_token,
            virtual_sol_reserves=self.v_sol,
            real_token_reserves=self.r_token,
            real_sol_reserves=self.r_sol,
            token_total_supply=1_000_000_000_000_000,
            complete=False,
        )
        assert AMMQuoteCalculator.check_dynamic_liquidity_floor(curve) is True

        # Extremely illiquid pool: 0.01 SOL reserve
        illiquid_curve = BondingCurveState(
            virtual_token_reserves=1000,
            virtual_sol_reserves=10_000_000,  # 0.01 SOL
            real_token_reserves=100,
            real_sol_reserves=10_000_000,
            token_total_supply=1000,
            complete=False,
        )
        assert AMMQuoteCalculator.check_dynamic_liquidity_floor(illiquid_curve) is False


# =====================================================================
# 3. On-Chain Authority & Security Auditor Tests (todo-07)
# =====================================================================

class TestAuthorityAuditor:
    def _create_mock_mint_data(
        self,
        mint_revoked: bool = True,
        freeze_revoked: bool = True,
        supply: int = 1_000_000_000_000_000,
        decimals: int = 6,
    ) -> bytes:
        """Helper to create raw 82-byte SPL Mint account data."""
        mint_opt = 0 if mint_revoked else 1
        mint_auth = b"\x00" * 32 if mint_revoked else b"\x01" * 32
        freeze_opt = 0 if freeze_revoked else 1
        freeze_auth = b"\x00" * 32 if freeze_revoked else b"\x02" * 32

        return (
            struct.pack("<I", mint_opt)
            + mint_auth
            + struct.pack("<Q", supply)
            + struct.pack("<B", decimals)
            + struct.pack("<?", True)
            + struct.pack("<I", freeze_opt)
            + freeze_auth
        )

    def test_parse_spl_mint_data_clean(self):
        """Verify parsing clean revoked mint."""
        raw = self._create_mock_mint_data(mint_revoked=True, freeze_revoked=True)
        assert len(raw) == 82
        mint_state = parse_spl_mint_data(raw)
        assert mint_state.is_mint_revoked is True
        assert mint_state.is_freeze_revoked is True
        assert mint_state.supply == 1_000_000_000_000_000
        assert mint_state.decimals == 6

    def test_parse_spl_mint_data_honeypot(self):
        """Verify parsing mint with active freeze authority."""
        raw = self._create_mock_mint_data(mint_revoked=True, freeze_revoked=False)
        mint_state = parse_spl_mint_data(raw)
        assert mint_state.is_mint_revoked is True
        assert mint_state.is_freeze_revoked is False
        assert mint_state.freeze_authority is not None

    def test_top10_concentration_calculation(self):
        """Verify top 10 calculation excludes bonding curve address."""
        total_supply = 1_000_000_000
        curve_addr = "CurveAccount1111111111111111111111111111111"

        holders = [
            {"address": curve_addr, "amount": 800_000_000},  # 80% on bonding curve
            {"address": "HolderA", "amount": 20_000_000},    # 2%
            {"address": "HolderB", "amount": 15_000_000},    # 1.5%
            {"address": "HolderC", "amount": 10_000_000},    # 1%
        ]

        # Top retail holders sum = 45M = 4.5% of total supply
        conc = AuthorityAuditor.calculate_top10_concentration(
            total_supply=total_supply,
            largest_holders=holders,
            bonding_curve_address=curve_addr,
        )
        assert conc == 4.5

    def test_audit_candidate_passes_clean_token(self):
        """Verify full audit passes for compliant token."""
        raw_mint = self._create_mock_mint_data(mint_revoked=True, freeze_revoked=True)
        curve_addr = "CurveAccount1111111111111111111111111111111"
        holders = [
            {"address": curve_addr, "amount": 800_000_000},
            {"address": "HolderA", "amount": 50_000_000},  # 5%
        ]

        result = AuthorityAuditor.audit_candidate(
            mint_address="Mint111111111111111111111111111111111111111",
            raw_mint_bytes=raw_mint,
            largest_holders=holders,
            bonding_curve_address=curve_addr,
        )

        assert result.passed is True
        assert result.mint_revoked is True
        assert result.freeze_revoked is True
        assert result.top10_concentration_pct <= 25.0

    def test_audit_candidate_fails_active_mint(self):
        """Verify audit rejects unrevoked mint authority."""
        raw_mint = self._create_mock_mint_data(mint_revoked=False, freeze_revoked=True)
        result = AuthorityAuditor.audit_candidate(
            mint_address="Mint111111111111111111111111111111111111111",
            raw_mint_bytes=raw_mint,
            largest_holders=[],
            bonding_curve_address="Curve",
        )
        assert result.passed is False
        assert "Mint authority is active" in result.reason

    def test_audit_candidate_fails_active_freeze(self):
        """Verify audit rejects unrevoked freeze authority (honeypot risk)."""
        raw_mint = self._create_mock_mint_data(mint_revoked=True, freeze_revoked=False)
        result = AuthorityAuditor.audit_candidate(
            mint_address="Mint111111111111111111111111111111111111111",
            raw_mint_bytes=raw_mint,
            largest_holders=[],
            bonding_curve_address="Curve",
        )
        assert result.passed is False
        assert "Freeze authority is active" in result.reason

    def test_audit_candidate_fails_excessive_concentration(self):
        """Verify audit rejects non-curve top-10 concentration > 25.0%."""
        total_supply = 1_000_000_000
        raw_mint = self._create_mock_mint_data(mint_revoked=True, freeze_revoked=True, supply=total_supply)
        curve_addr = "CurveAccount1111111111111111111111111111111"
        holders = [
            {"address": curve_addr, "amount": 600_000_000},
            {"address": "Whale1", "amount": 300_000_000},  # 30% concentration (>25%)
        ]

        result = AuthorityAuditor.audit_candidate(
            mint_address="Mint111111111111111111111111111111111111111",
            raw_mint_bytes=raw_mint,
            largest_holders=holders,
            bonding_curve_address=curve_addr,
        )
        assert result.passed is False
        assert "Top 10 concentration excessive" in result.reason
