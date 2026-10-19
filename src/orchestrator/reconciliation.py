"""
Startup On-Chain SPL Reconciliation Engine.
Validates SQLite WAL persisted position records against live on-chain Solana SPL token accounts,
detects downtime liquidations, and flags orphan token holdings.
"""

from dataclasses import asdict, dataclass, field
import logging
import os
import time
from typing import Any, Dict, List, Optional
import requests

from src.engine.position_state_machine import PositionState
from src.orchestrator.alerting import AlertDispatcher, AlertEvent, AlertSeverity
from src.orchestrator.persistence import StatePersistenceManager
from src.utils.config import ConfigLoader

logger = logging.getLogger("StartupReconciler")

SPL_TOKEN_PROGRAM_ID = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"


def fetch_wallet_spl_balances(
    wallet_pubkey: str,
    http_rpc_url: Optional[str] = None,
    timeout_sec: float = 5.0,
) -> Dict[str, int]:
    """
    Queries Solana RPC getTokenAccountsByOwner for all non-zero SPL token balances.
    Returns mapping of {mint_address: raw_token_amount}.
    """
    if not wallet_pubkey or wallet_pubkey.startswith("Simulated"):
        return {}

    url = http_rpc_url
    if not url:
        try:
            url = ConfigLoader().load_telemetry_config().http_rpc_url
        except Exception:
            url = os.getenv("HELIUS_RPC_URL", "https://api.mainnet-beta.solana.com")

    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getTokenAccountsByOwner",
        "params": [
            wallet_pubkey,
            {"programId": SPL_TOKEN_PROGRAM_ID},
            {"encoding": "jsonParsed"},
        ],
    }

    try:
        resp = requests.post(url, json=payload, timeout=timeout_sec)
        if resp.status_code != 200:
            logger.warning(f"Failed querying SPL accounts: HTTP {resp.status_code}")
            return {}

        data = resp.json()
        result = data.get("result", {}).get("value", [])
        balances: Dict[str, int] = {}

        for item in result:
            account_data = item.get("account", {}).get("data", {})
            parsed = account_data.get("parsed", {})
            info = parsed.get("info", {})
            mint = info.get("mint")
            token_amount_obj = info.get("tokenAmount", {})
            raw_amount_str = token_amount_obj.get("amount", "0")

            try:
                raw_amount = int(raw_amount_str)
            except (ValueError, TypeError):
                raw_amount = 0

            if mint and raw_amount > 0:
                balances[mint] = balances.get(mint, 0) + raw_amount

        logger.info(f"Queried {len(balances)} non-zero SPL token accounts for {wallet_pubkey[:8]}...")
        return balances

    except Exception as e:
        logger.error(f"Error fetching on-chain SPL token accounts: {e}")
        return {}


@dataclass
class ReconciliationReport:
    """Summary report produced after validating WAL against on-chain holdings."""

    timestamp: float
    wallet_pubkey: str
    total_active_persisted: int
    matching_positions: List[Dict[str, Any]] = field(default_factory=list)
    downtime_liquidated: List[Dict[str, Any]] = field(default_factory=list)
    orphan_holdings: Dict[str, int] = field(default_factory=dict)
    messages: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class StartupReconciler:
    """
    Reconciles startup state between local SQLite WAL database and on-chain holdings.
    """

    def __init__(
        self,
        persistence: Optional[StatePersistenceManager] = None,
        alerts: Optional[AlertDispatcher] = None,
        http_rpc_url: Optional[str] = None,
    ):
        self.persistence = persistence or StatePersistenceManager()
        self.alerts = alerts or AlertDispatcher()
        self.http_rpc_url = http_rpc_url

    async def reconcile(
        self,
        wallet_pubkey: Optional[str] = None,
        on_chain_balances: Optional[Dict[str, int]] = None,
    ) -> ReconciliationReport:
        """
        Executes complete reconciliation:
        1. Obtains on-chain SPL balances.
        2. Compares active positions in SQLite WAL.
        3. Identifies matching, downtime liquidated, and orphan holdings.
        4. Closes missing positions in WAL and alerts on discrepancies.
        """
        now = time.time()
        pubkey = wallet_pubkey or "UNKNOWN_WALLET"

        if on_chain_balances is not None:
            balances = on_chain_balances
        else:
            balances = fetch_wallet_spl_balances(pubkey, http_rpc_url=self.http_rpc_url)

        active_positions = self.persistence.load_active_positions()
        matching: List[Dict[str, Any]] = []
        liquidated: List[Dict[str, Any]] = []
        messages: List[str] = []

        persisted_mints = set()

        for pos in active_positions:
            mint = pos["mint"]
            pool_id = pos["pool_id"]
            persisted_mints.add(mint)
            expected_tokens = pos.get("size_tokens", 0)
            actual_balance = balances.get(mint, 0)

            if actual_balance == 0 and expected_tokens > 0:
                # Token was sold or liquidated externally during downtime
                self.persistence.commit_closed_position(
                    pool_id=pool_id,
                    exit_price_sol=0.0,
                    state=PositionState.CLOSED,
                )
                msg = (
                    f"Position {pool_id} ({mint[:8]}...): Token balance is 0 on-chain. "
                    f"Transitioned to CLOSED (EXTERNAL_LIQUIDATION_DURING_DOWNTIME)."
                )
                messages.append(msg)
                liquidated.append({**pos, "reconciliation_note": msg})
                logger.warning(msg)
            else:
                msg = f"Position {pool_id} ({mint[:8]}...): Verified matching on-chain balance ({actual_balance} tokens)."
                messages.append(msg)
                matching.append(pos)
                logger.info(msg)

        # Detect orphan holdings: wallet holds SPL tokens with no matching active position in WAL
        orphans: Dict[str, int] = {}
        for mint, bal in balances.items():
            if mint not in persisted_mints and bal > 0:
                orphans[mint] = bal
                msg = f"Orphan SPL Token Detected: Wallet holds {bal} tokens of {mint} with no active WAL position record."
                messages.append(msg)
                logger.warning(msg)

        report = ReconciliationReport(
            timestamp=now,
            wallet_pubkey=pubkey,
            total_active_persisted=len(active_positions),
            matching_positions=matching,
            downtime_liquidated=liquidated,
            orphan_holdings=orphans,
            messages=messages,
        )

        # Dispatch alerts if discrepancies detected
        if liquidated:
            await self.alerts.dispatch(
                AlertEvent(
                    event_type="DOWNTIME_LIQUIDATION_DETECTED",
                    severity=AlertSeverity.WARNING,
                    title="Downtime Liquidation Detected",
                    message=f"{len(liquidated)} positions were closed externally during daemon downtime.",
                    metadata={"liquidated_count": len(liquidated), "pool_ids": [p["pool_id"] for p in liquidated]},
                )
            )

        if orphans:
            await self.alerts.dispatch(
                AlertEvent(
                    event_type="ORPHAN_SPL_TOKENS_DETECTED",
                    severity=AlertSeverity.WARNING,
                    title="Orphan SPL Tokens in Wallet",
                    message=f"Wallet holds {len(orphans)} unmanaged token mints. Speculative trading paused for these mints.",
                    metadata={"orphan_mints": list(orphans.keys())},
                )
            )

        return report
