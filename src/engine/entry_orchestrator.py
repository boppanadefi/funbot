"""
The 12-Check Entry Decision Orchestrator for Post-Graduation Momentum.
Enforces 100% PASS Gate across all 12 structural, flow, security, AMM, and risk criteria.

The 12 Checks:
1. Age Window (8.0m to 25.0m post-graduation cooldown)
2. Canonical Pair (quote_mint == WSOL, canonical PumpSwap program ID)
3. Velocity of Unique Wallets (VUW >= 1.20, or 1.05 in REGIME_TEST)
4. Aggression Ratio (Taker Buy SOL / Taker Sell SOL > 2.00)
5. Baseline Demand Floor (60s net taker buy >= $100/min, or $50/min in test)
6. Freeze Authority permanently revoked (freeze_authority_option == 0)
7. Mint Authority permanently revoked (mint_authority_option == 0)
8. Top-10 Concentration (retail holders <= 25.0% excluding bonding curve)
9. Dynamic Liquidity Floor (swap >= $3.00 with price impact <= 1.5%)
10. Pre-Trade Risk Capacity (Stressed Loss + Exit Reserve <= Remaining Daily Budget)
11. Emergency Exit Reserve (Wallet balance post-trade maintains >= 0.005 SOL)
12. WebSocket Data Freshness (Last WS message latency <= 1000 ms)
"""

from dataclasses import dataclass, field
from enum import Enum
import logging
import time
from typing import Any, Dict, List, Optional

from src.amm.quote_calculator import AMMQuoteCalculator
from src.protocol.pumpswap import BondingCurveState, PUMPSWAP_PROGRAM_ID_STR
from src.security.authority_auditor import AuthorityAuditResult

logger = logging.getLogger("EntryOrchestrator")

WSOL_MINT_STR = "So11111111111111111111111111111111111111112"
MIN_EXIT_RESERVE_SOL = 0.005
MAX_WS_LATENCY_MS = 1000.0


class CheckStatus(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    SKIPPED = "SKIPPED"


@dataclass(frozen=True)
class IndividualCheckResult:
    check_number: int
    name: str
    status: CheckStatus
    value: Any
    threshold: Any
    message: str


@dataclass(frozen=True)
class EntryEvaluationResult:
    passed: bool
    pool_id: str
    timestamp: float
    checks: Dict[str, IndividualCheckResult]
    rejection_reasons: List[str]
    suggested_size_sol: float
    is_regime_test: bool = False

    @property
    def passed_count(self) -> int:
        return sum(1 for c in self.checks.values() if c.status == CheckStatus.PASS)

    @property
    def failed_count(self) -> int:
        return sum(1 for c in self.checks.values() if c.status == CheckStatus.FAIL)


@dataclass
class CandidateContext:
    """Consolidated context payload for 12-check evaluation."""
    pool_id: str
    quote_mint: str
    program_id: str
    elapsed_minutes: float
    vuw: float
    aggression_ratio: float
    net_buy_volume_usd_60s: float
    authority_audit: AuthorityAuditResult
    bonding_curve: BondingCurveState
    sol_price_usd: float
    wallet_balance_sol: float
    remaining_daily_budget_usd: float
    circuit_breaker_halted: bool
    last_ws_message_age_ms: float
    is_regime_test: bool = False
    requested_size_usd: float = 25.0


class EntryDecisionOrchestrator:
    """Evaluates candidate pools against the exact 12-Check Gate."""

    @classmethod
    def evaluate(cls, ctx: CandidateContext) -> EntryEvaluationResult:
        checks: Dict[str, IndividualCheckResult] = {}
        reasons: List[str] = []

        # ----------------------------------------------------
        # Check 1: Age Window (8.0m to 25.0m post-graduation)
        # ----------------------------------------------------
        c1_pass = 8.0 <= ctx.elapsed_minutes <= 25.0
        checks["01_age_window"] = IndividualCheckResult(
            check_number=1,
            name="Age Window",
            status=CheckStatus.PASS if c1_pass else CheckStatus.FAIL,
            value=round(ctx.elapsed_minutes, 2),
            threshold="[8.0m, 25.0m]",
            message=f"Elapsed {ctx.elapsed_minutes:.1f}m within 8-25m window" if c1_pass else f"Elapsed {ctx.elapsed_minutes:.1f}m outside 8-25m window",
        )
        if not c1_pass:
            reasons.append(f"Check 1 Failed: Elapsed {ctx.elapsed_minutes:.1f}m outside 8-25m post-graduation window")

        # ----------------------------------------------------
        # Check 2: Canonical Pair (quote == WSOL, canonical program ID)
        # ----------------------------------------------------
        c2_pass = (ctx.quote_mint == WSOL_MINT_STR) and (ctx.program_id == PUMPSWAP_PROGRAM_ID_STR)
        checks["02_canonical_pair"] = IndividualCheckResult(
            check_number=2,
            name="Canonical Pair",
            status=CheckStatus.PASS if c2_pass else CheckStatus.FAIL,
            value={"quote": ctx.quote_mint[:8], "program": ctx.program_id[:8]},
            threshold={"quote": WSOL_MINT_STR[:8], "program": PUMPSWAP_PROGRAM_ID_STR[:8]},
            message="Canonical WSOL quote and PumpSwap program verified" if c2_pass else "Non-canonical pair or program ID",
        )
        if not c2_pass:
            reasons.append("Check 2 Failed: Non-canonical quote mint or program ID")

        # ----------------------------------------------------
        # Check 3: Velocity of Unique Wallets (VUW >= 1.20, or 1.05 in test)
        # ----------------------------------------------------
        vuw_thresh = 1.05 if ctx.is_regime_test else 1.20
        c3_pass = ctx.vuw >= vuw_thresh
        checks["03_velocity_unique_wallets"] = IndividualCheckResult(
            check_number=3,
            name="Velocity of Unique Wallets",
            status=CheckStatus.PASS if c3_pass else CheckStatus.FAIL,
            value=round(ctx.vuw, 2),
            threshold=vuw_thresh,
            message=f"VUW {ctx.vuw:.2f} >= {vuw_thresh:.2f}" if c3_pass else f"VUW {ctx.vuw:.2f} < {vuw_thresh:.2f}",
        )
        if not c3_pass:
            reasons.append(f"Check 3 Failed: VUW {ctx.vuw:.2f} below threshold {vuw_thresh:.2f}")

        # ----------------------------------------------------
        # Check 4: Aggression Ratio (Buy SOL / Sell SOL > 2.00)
        # ----------------------------------------------------
        c4_pass = ctx.aggression_ratio > 2.00
        checks["04_aggression_ratio"] = IndividualCheckResult(
            check_number=4,
            name="Aggression Ratio",
            status=CheckStatus.PASS if c4_pass else CheckStatus.FAIL,
            value=round(ctx.aggression_ratio, 2),
            threshold=2.00,
            message=f"Aggression ratio {ctx.aggression_ratio:.2f} > 2.00" if c4_pass else f"Aggression ratio {ctx.aggression_ratio:.2f} <= 2.00",
        )
        if not c4_pass:
            reasons.append(f"Check 4 Failed: Aggression ratio {ctx.aggression_ratio:.2f} <= 2.00")

        # ----------------------------------------------------
        # Check 5: Baseline Demand Floor (60s net taker buy >= $100/m, or $50/m in test)
        # ----------------------------------------------------
        demand_thresh = 50.0 if ctx.is_regime_test else 100.0
        c5_pass = ctx.net_buy_volume_usd_60s >= demand_thresh
        checks["05_baseline_demand_floor"] = IndividualCheckResult(
            check_number=5,
            name="Baseline Demand Floor",
            status=CheckStatus.PASS if c5_pass else CheckStatus.FAIL,
            value=round(ctx.net_buy_volume_usd_60s, 2),
            threshold=demand_thresh,
            message=f"60s Net buy ${ctx.net_buy_volume_usd_60s:.1f}/min >= ${demand_thresh:.1f}" if c5_pass else f"60s Net buy ${ctx.net_buy_volume_usd_60s:.1f}/min < ${demand_thresh:.1f}",
        )
        if not c5_pass:
            reasons.append(f"Check 5 Failed: 60s Demand ${ctx.net_buy_volume_usd_60s:.1f}/min below floor ${demand_thresh:.1f}")

        # ----------------------------------------------------
        # Check 6: Freeze Authority Permanently Revoked
        # ----------------------------------------------------
        c6_pass = ctx.authority_audit.freeze_revoked
        checks["06_freeze_authority"] = IndividualCheckResult(
            check_number=6,
            name="Freeze Authority Revoked",
            status=CheckStatus.PASS if c6_pass else CheckStatus.FAIL,
            value="REVOKED" if c6_pass else "ACTIVE",
            threshold="REVOKED",
            message="Freeze authority permanently revoked" if c6_pass else "Freeze authority active (honeypot risk)",
        )
        if not c6_pass:
            reasons.append("Check 6 Failed: Freeze authority active on mint")

        # ----------------------------------------------------
        # Check 7: Mint Authority Permanently Revoked
        # ----------------------------------------------------
        c7_pass = ctx.authority_audit.mint_revoked
        checks["07_mint_authority"] = IndividualCheckResult(
            check_number=7,
            name="Mint Authority Revoked",
            status=CheckStatus.PASS if c7_pass else CheckStatus.FAIL,
            value="REVOKED" if c7_pass else "ACTIVE",
            threshold="REVOKED",
            message="Mint authority permanently revoked" if c7_pass else "Mint authority active (inflation risk)",
        )
        if not c7_pass:
            reasons.append("Check 7 Failed: Mint authority active on mint")

        # ----------------------------------------------------
        # Check 8: Top-10 Concentration (<= 25.0% excluding bonding curve)
        # ----------------------------------------------------
        c8_pass = ctx.authority_audit.top10_concentration_pct <= 25.0
        checks["08_top10_concentration"] = IndividualCheckResult(
            check_number=8,
            name="Top-10 Concentration",
            status=CheckStatus.PASS if c8_pass else CheckStatus.FAIL,
            value=round(ctx.authority_audit.top10_concentration_pct, 2),
            threshold=25.0,
            message=f"Top 10 concentration {ctx.authority_audit.top10_concentration_pct:.1f}% <= 25.0%" if c8_pass else f"Top 10 concentration {ctx.authority_audit.top10_concentration_pct:.1f}% > 25.0%",
        )
        if not c8_pass:
            reasons.append(f"Check 8 Failed: Top 10 concentration {ctx.authority_audit.top10_concentration_pct:.1f}% exceeds 25.0%")

        # ----------------------------------------------------
        # Check 9: Dynamic Liquidity Floor (swap >= $3.00 with impact <= 1.5%)
        # ----------------------------------------------------
        test_swap_usd = 2.0 if ctx.is_regime_test else 3.0
        sol_price = max(1.0, ctx.sol_price_usd)
        test_swap_lamports = int((test_swap_usd / sol_price) * 1_000_000_000)
        c9_pass = AMMQuoteCalculator.check_dynamic_liquidity_floor(
            curve_state=ctx.bonding_curve,
            test_swap_lamports=test_swap_lamports,
            max_allowed_impact_pct=1.5,
        )
        checks["09_dynamic_liquidity_floor"] = IndividualCheckResult(
            check_number=9,
            name="Dynamic Liquidity Floor",
            status=CheckStatus.PASS if c9_pass else CheckStatus.FAIL,
            value=f"${test_swap_usd:.2f} swap",
            threshold="impact <= 1.5%",
            message="Pool liquidity accommodates trade size with <= 1.5% impact" if c9_pass else "Pool liquidity insufficient (impact > 1.5%)",
        )
        if not c9_pass:
            reasons.append(f"Check 9 Failed: AMM price impact exceeds 1.5% on ${test_swap_usd:.2f} swap")

        # ----------------------------------------------------
        # Check 10: Pre-Trade Risk Capacity (Stressed Loss + Exit <= Budget)
        # ----------------------------------------------------
        # Stressed loss = 15% of position + $0.50 slippage buffer
        stressed_loss_usd = (ctx.requested_size_usd * 0.15) + 0.50
        exit_reserve_usd = MIN_EXIT_RESERVE_SOL * sol_price
        c10_pass = (not ctx.circuit_breaker_halted) and ((stressed_loss_usd + exit_reserve_usd) <= ctx.remaining_daily_budget_usd)
        checks["10_risk_capacity"] = IndividualCheckResult(
            check_number=10,
            name="Pre-Trade Risk Capacity",
            status=CheckStatus.PASS if c10_pass else CheckStatus.FAIL,
            value=round(stressed_loss_usd + exit_reserve_usd, 2),
            threshold=round(ctx.remaining_daily_budget_usd, 2),
            message=f"Risk demand ${stressed_loss_usd + exit_reserve_usd:.2f} fits within remaining budget ${ctx.remaining_daily_budget_usd:.2f}" if c10_pass else "Exceeds daily risk budget or circuit breaker halted",
        )
        if not c10_pass:
            reasons.append("Check 10 Failed: Pre-trade risk demand exceeds remaining daily budget")

        # ----------------------------------------------------
        # Check 11: Emergency Exit Reserve (Wallet balance post-trade >= 0.005 SOL)
        # ----------------------------------------------------
        position_size_sol = ctx.requested_size_usd / sol_price
        post_trade_balance_sol = ctx.wallet_balance_sol - position_size_sol
        c11_pass = post_trade_balance_sol >= MIN_EXIT_RESERVE_SOL
        checks["11_exit_reserve"] = IndividualCheckResult(
            check_number=11,
            name="Emergency Exit Reserve",
            status=CheckStatus.PASS if c11_pass else CheckStatus.FAIL,
            value=round(post_trade_balance_sol, 4),
            threshold=MIN_EXIT_RESERVE_SOL,
            message=f"Post-trade balance {post_trade_balance_sol:.4f} SOL >= {MIN_EXIT_RESERVE_SOL} SOL" if c11_pass else f"Post-trade balance {post_trade_balance_sol:.4f} SOL < {MIN_EXIT_RESERVE_SOL} SOL",
        )
        if not c11_pass:
            reasons.append(f"Check 11 Failed: Wallet balance after trade fails to retain {MIN_EXIT_RESERVE_SOL} SOL exit reserve")

        # ----------------------------------------------------
        # Check 12: WebSocket Data Freshness (<= 1000 ms)
        # ----------------------------------------------------
        c12_pass = ctx.last_ws_message_age_ms <= MAX_WS_LATENCY_MS
        checks["12_ws_freshness"] = IndividualCheckResult(
            check_number=12,
            name="WebSocket Data Freshness",
            status=CheckStatus.PASS if c12_pass else CheckStatus.FAIL,
            value=round(ctx.last_ws_message_age_ms, 1),
            threshold=MAX_WS_LATENCY_MS,
            message=f"WS latency {ctx.last_ws_message_age_ms:.1f}ms <= {MAX_WS_LATENCY_MS}ms" if c12_pass else f"WS latency {ctx.last_ws_message_age_ms:.1f}ms > {MAX_WS_LATENCY_MS}ms (stale feed)",
        )
        if not c12_pass:
            reasons.append(f"Check 12 Failed: WebSocket data stale ({ctx.last_ws_message_age_ms:.0f}ms > {MAX_WS_LATENCY_MS:.0f}ms)")

        # ----------------------------------------------------
        # Gate Synthesis: 100% PASS Required
        # ----------------------------------------------------
        all_passed = len(reasons) == 0
        suggested_size_sol = position_size_sol if all_passed else 0.0

        return EntryEvaluationResult(
            passed=all_passed,
            pool_id=ctx.pool_id,
            timestamp=time.time(),
            checks=checks,
            rejection_reasons=reasons,
            suggested_size_sol=round(suggested_size_sol, 4),
            is_regime_test=ctx.is_regime_test,
        )
