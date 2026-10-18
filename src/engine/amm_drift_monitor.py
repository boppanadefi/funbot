"""
AMM Drift Monitor (Local Virtual AMM Mirror vs. RPC Ground-Truth).
Every 60 seconds, queries RPC pool reserves (via getTokenAccounts / account data)
and compares them to the local Virtual AMM constant-product reserves.
If drift exceeds 0.1%, logs a critical failure.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
import logging
from typing import Dict, List, Optional, Tuple

from src.engine.virtual_amm import VirtualAMMMirror

logger = logging.getLogger("AMMDriftMonitor")


@dataclass
class AMMDriftReport:
    pool_address: str
    timestamp: datetime
    local_sol_reserve: float
    local_token_reserve: float
    rpc_sol_reserve: float
    rpc_token_reserve: float
    sol_drift_pct: float
    token_drift_pct: float
    max_drift_pct: float
    is_critical_failure: bool
    status_message: str


class AMMDriftMonitor:
    """
    Monitors drift between Local Virtual AMM Mirror and real-world RPC pool reserves.
    Enforces the < 0.1% drift tolerance ceiling.
    """

    DRIFT_TOLERANCE_PCT: float = 0.10  # 0.1% drift ceiling

    def __init__(
        self,
        virtual_amm: Optional[VirtualAMMMirror] = None,
        check_interval_seconds: float = 60.0,
    ):
        self.virtual_amm = virtual_amm or VirtualAMMMirror()
        self.check_interval_seconds = check_interval_seconds
        self.reports: List[AMMDriftReport] = []
        self.critical_failures: List[AMMDriftReport] = []
        self._last_check_times: Dict[str, datetime] = {}

    def check_pool_drift(
        self,
        pool_address: str,
        rpc_sol_reserve: float,
        rpc_token_reserve: float,
        check_time: Optional[datetime] = None,
    ) -> AMMDriftReport:
        """
        Calculates reserve drift between Local Virtual AMM and RPC ground truth.
        Drift formula: |Local - RPC| / RPC * 100
        """
        now = check_time or datetime.now(timezone.utc)
        self._last_check_times[pool_address] = now

        pool = self.virtual_amm.get_pool(pool_address)
        if not pool:
            # Register pool if not currently tracked
            pool = self.virtual_amm.register_pool(
                pool_address=pool_address,
                token_mint="UNKNOWN",
                initial_sol_reserve=rpc_sol_reserve,
                initial_token_reserve=rpc_token_reserve,
            )

        local_sol = pool.sol_reserve
        local_tokens = pool.token_reserve

        sol_drift = abs(local_sol - rpc_sol_reserve) / rpc_sol_reserve * 100.0 if rpc_sol_reserve > 0 else 0.0
        tok_drift = abs(local_tokens - rpc_token_reserve) / rpc_token_reserve * 100.0 if rpc_token_reserve > 0 else 0.0
        max_drift = max(sol_drift, tok_drift)

        is_critical = max_drift > self.DRIFT_TOLERANCE_PCT

        if is_critical:
            msg = (
                f"CRITICAL FAILURE: AMM Drift {max_drift:.4f}% exceeds {self.DRIFT_TOLERANCE_PCT:.2f}% threshold "
                f"on {pool_address}! Local: ({local_sol:.4f} SOL, {local_tokens:,.0f} tokens) vs "
                f"RPC: ({rpc_sol_reserve:.4f} SOL, {rpc_token_reserve:,.0f} tokens)."
            )
            logger.critical(msg)
        else:
            msg = (
                f"AMM Sync Normal: Drift {max_drift:.4f}% within {self.DRIFT_TOLERANCE_PCT:.2f}% threshold "
                f"on {pool_address}."
            )
            logger.debug(msg)

        report = AMMDriftReport(
            pool_address=pool_address,
            timestamp=now,
            local_sol_reserve=round(local_sol, 4),
            local_token_reserve=round(local_tokens, 2),
            rpc_sol_reserve=round(rpc_sol_reserve, 4),
            rpc_token_reserve=round(rpc_token_reserve, 2),
            sol_drift_pct=round(sol_drift, 4),
            token_drift_pct=round(tok_drift, 4),
            max_drift_pct=round(max_drift, 4),
            is_critical_failure=is_critical,
            status_message=msg,
        )

        self.reports.append(report)
        if is_critical:
            self.critical_failures.append(report)

        return report

    def is_check_due(self, pool_address: str, now: Optional[datetime] = None) -> bool:
        """Determines if 60 seconds have elapsed since last check for this pool."""
        t = now or datetime.now(timezone.utc)
        last_t = self._last_check_times.get(pool_address)
        if not last_t:
            return True
        return (t - last_t).total_seconds() >= self.check_interval_seconds

    def has_critical_failure(self) -> bool:
        return len(self.critical_failures) > 0

    def get_latest_report(self, pool_address: Optional[str] = None) -> Optional[AMMDriftReport]:
        if pool_address:
            matching = [r for r in self.reports if r.pool_address == pool_address]
            return matching[-1] if matching else None
        return self.reports[-1] if self.reports else None
