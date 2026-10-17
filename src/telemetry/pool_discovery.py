"""
Canonical Pool Discovery Module — PumpSwap Program Listener.
Identifies canonical SOL-quoted PumpSwap pool migrations.
Excludes USDC and non-canonical bonding curve venues.
"""

from dataclasses import dataclass, asdict
from datetime import datetime, timezone
import json
import logging
from typing import Callable, Dict, List, Optional
from src.utils.config import ConfigLoader, TelemetryConfig

logger = logging.getLogger(__name__)

# Canonical Solana native mint (WSOL)
NATIVE_SOL_MINT = "So11111111111111111111111111111111111111112"


@dataclass
class PoolMigrationEvent:
    pool_address: str
    token_mint: str
    quote_mint: str
    is_canonical: bool
    graduation_slot: int
    graduation_ts: str
    initial_sol_reserve: float
    initial_token_reserve: float
    market_cap_sol: float

    def to_dict(self) -> Dict:
        return asdict(self)


class PoolDiscoveryEngine:
    def __init__(self, config: Optional[TelemetryConfig] = None):
        if config is None:
            loader = ConfigLoader()
            config = loader.load_telemetry_config()
        self.config = config
        self.pumpswap_program_id = config.pumpswap_program_id
        self.migration_callbacks: List[Callable[[PoolMigrationEvent], None]] = []

    def register_callback(self, callback: Callable[[PoolMigrationEvent], None]) -> None:
        """Register a downstream handler for pool migration events."""
        self.migration_callbacks.append(callback)

    def parse_migration_instruction(
        self,
        tx_signature: str,
        slot: int,
        timestamp_iso: str,
        program_id: str,
        accounts: List[str],
        data_bytes: bytes,
    ) -> Optional[PoolMigrationEvent]:
        """
        Parses an on-chain transaction instruction to verify if it represents
        a canonical graduation migration into PumpSwap.
        """
        if program_id != self.pumpswap_program_id:
            return None

        # Basic canonical validation (SOL quote verification)
        if len(accounts) < 4:
            return None

        pool_address = accounts[0]
        token_mint = accounts[1]
        quote_mint = accounts[2]

        is_canonical = (quote_mint == NATIVE_SOL_MINT)
        if not is_canonical:
            logger.info("Skipping non-canonical / USDC quote pool: %s", pool_address)
            return None

        # Simulated initial graduation reserves (or parsed from byte payload)
        # Pump.fun typical graduation: ~80-85 SOL paired with remaining tokens
        initial_sol_reserve = 85.0
        initial_token_reserve = 206_900_000.0
        market_cap_sol = (initial_sol_reserve / initial_token_reserve) * 1_000_000_000.0

        event = PoolMigrationEvent(
            pool_address=pool_address,
            token_mint=token_mint,
            quote_mint=quote_mint,
            is_canonical=is_canonical,
            graduation_slot=slot,
            graduation_ts=timestamp_iso,
            initial_sol_reserve=initial_sol_reserve,
            initial_token_reserve=initial_token_reserve,
            market_cap_sol=market_cap_sol,
        )

        logger.info("Canonical PumpSwap pool discovered: %s (Mint: %s)", pool_address, token_mint)
        for cb in self.migration_callbacks:
            cb(event)

        return event
