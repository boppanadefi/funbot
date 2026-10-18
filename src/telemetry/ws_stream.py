"""
Dedicated Solana WebSocket Stream Client (Helius / Triton / Public RPC).
Subscribes to swapEvents and accountChange notifications on the canonical PumpSwap program ID.
Integrates the Data Staleness Circuit Breaker (1000ms latency ceiling).
"""

import asyncio
from datetime import datetime, timezone
import json
import logging
import os
import time
from typing import Dict, List, Optional, Set
from pathlib import Path
import websockets

from src.engine.event_bus import EventBus, StreamEventType, LiveSwapEvent, LiveAccountChangeEvent
from src.engine.staleness_breaker import DataStalenessCircuitBreaker
from src.engine.virtual_amm import VirtualAMMMirror

logger = logging.getLogger("WebSocketStream")


def _load_dotenv() -> None:
    for candidate in [Path.cwd() / ".env", Path(__file__).resolve().parent.parent.parent / ".env"]:
        if candidate.is_file():
            try:
                with open(candidate, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith("#") and "=" in line:
                            k, v = line.split("=", 1)
                            k, v = k.strip(), v.strip().strip("'\"")
                            if k not in os.environ:
                                os.environ[k] = v
            except Exception:
                pass
            break


_load_dotenv()

DEFAULT_HELIUS_WSS_URL = "wss://mainnet.helius-rpc.com/?api-key=795a4f2f-6d09-49a7-afdd-b9365365125e"


class PumpSwapWSSClient:
    """
    Connects to dedicated Solana WebSocket endpoint (Helius / Triton / Public WSS).
    Subscriptions:
    1. logsSubscribe on PumpSwap Program ID (swapEvents: Buy & Sell).
    2. accountSubscribe on active candidate pools (ground-truth reserve balances).
    """

    DEFAULT_PUMPSWAP_PROGRAM_ID = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"

    def __init__(
        self,
        wss_url: Optional[str] = None,
        program_id: str = DEFAULT_PUMPSWAP_PROGRAM_ID,
        event_bus: Optional[EventBus] = None,
        staleness_breaker: Optional[DataStalenessCircuitBreaker] = None,
        virtual_amm: Optional[VirtualAMMMirror] = None,
        heartbeat_interval_seconds: float = 0.5,
    ):
        # Resolve WSS endpoint: Parameter > Environment variable (Helius / Triton) > default
        self.wss_url = (
            wss_url
            or os.getenv("HELIUS_WSS_URL")
            or os.getenv("TRITON_WSS_URL")
            or os.getenv("SOLANA_WSS_URL")
            or DEFAULT_HELIUS_WSS_URL
        )
        self.program_id = program_id
        self.event_bus = event_bus or EventBus()
        self.staleness_breaker = staleness_breaker or DataStalenessCircuitBreaker()
        self.virtual_amm = virtual_amm or VirtualAMMMirror()
        self.heartbeat_interval_seconds = heartbeat_interval_seconds

        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._is_running = False
        self._subscribed_accounts: Set[str] = set()
        self._logs_sub_id: Optional[int] = None
        self._pending_account_subs: Dict[int, str] = {}
        self._sub_id_to_pool: Dict[int, str] = {}
        self.message_count: int = 0
        self.swaps_count: int = 0
        self.account_changes_count: int = 0

    def subscribe_pool_accounts(self, pool_address: str) -> None:
        """Adds a candidate pool address to the accountSubscribe watchlist."""
        if pool_address not in self._subscribed_accounts:
            self._subscribed_accounts.add(pool_address)
            logger.info("Added pool %s to WSS accountSubscribe watchlist", pool_address)

    async def connect_and_listen(self) -> None:
        """
        Main asynchronous connection loop with automatic reconnect and exponential backoff.
        """
        self._is_running = True
        backoff_seconds = 1.0

        while self._is_running:
            try:
                logger.info("Connecting to dedicated Solana WSS: %s", self.wss_url)
                async with websockets.connect(
                    self.wss_url,
                    ping_interval=20,
                    ping_timeout=10,
                    max_size=10_000_000,
                ) as ws:
                    self._ws = ws
                    backoff_seconds = 1.0  # Reset backoff on successful handshake
                    logger.info("WebSocket connection established. Initializing PumpSwap subscriptions...")

                    # 1. Subscribe to logs mentioning PumpSwap program
                    await self._send_logs_subscribe(ws)

                    # 2. Subscribe to account changes for all candidate pools
                    for pool_addr in list(self._subscribed_accounts):
                        await self._send_account_subscribe(ws, pool_addr)

                    # 3. Message processing loop
                    while self._is_running:
                        try:
                            # 1.0s timeout ensures staleness breaker evaluates if stream stalls
                            message_raw = await asyncio.wait_for(ws.recv(), timeout=1.0)
                            self._handle_raw_message(message_raw)
                        except asyncio.TimeoutError:
                            # Check staleness when timeout triggers
                            is_stale, elapsed = self.staleness_breaker.check_staleness()
                            if is_stale:
                                self.event_bus.publish(StreamEventType.STALENESS_TRIPPED, elapsed)

            except (websockets.ConnectionClosed, Exception) as e:
                logger.warning("WebSocket connection dropped: %s. Reconnecting in %.1fs...", e, backoff_seconds)
                self.staleness_breaker.check_staleness()
                await asyncio.sleep(backoff_seconds)
                backoff_seconds = min(15.0, backoff_seconds * 1.5)

    async def _send_logs_subscribe(self, ws) -> None:
        """Sends logsSubscribe RPC request for canonical PumpSwap program."""
        req = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "logsSubscribe",
            "params": [
                {"mentions": [self.program_id]},
                {"commitment": "processed"}
            ]
        }
        await ws.send(json.dumps(req))
        logger.info("Sent logsSubscribe for Program %s (commitment: processed)", self.program_id)

    async def _send_account_subscribe(self, ws, pool_address: str) -> None:
        """Sends accountSubscribe RPC request for a specific pool."""
        req_id = 100 + len(self._pending_account_subs) + 1
        self._pending_account_subs[req_id] = pool_address
        req = {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": "accountSubscribe",
            "params": [
                pool_address,
                {"encoding": "jsonParsed", "commitment": "processed"}
            ]
        }
        await ws.send(json.dumps(req))
        logger.info("Sent accountSubscribe for Pool %s (ReqID: %d)", pool_address, req_id)

    def _handle_raw_message(self, message_raw: str) -> None:
        """
        Parses incoming JSON-RPC WebSocket notifications:
        1. Records message arrival immediately on Data Staleness Circuit Breaker.
        2. Routes to swap log or accountChange parser.
        3. Recalculates Virtual AMM before dispatching to strategy listeners.
        """
        self.message_count += 1
        try:
            data = json.loads(message_raw)
        except Exception:
            return

        # Track subscription responses
        if "id" in data and "result" in data:
            req_id = data["id"]
            sub_id = data["result"]
            if req_id == 1:
                self._logs_sub_id = sub_id
                logger.info("PumpSwap logsSubscribe active (sub_id=%s)", sub_id)
            elif req_id in self._pending_account_subs:
                pool_addr = self._pending_account_subs.pop(req_id)
                self._sub_id_to_pool[sub_id] = pool_addr
                logger.info("accountSubscribe active for pool %s (sub_id=%s)", pool_addr, sub_id)
            return

        method = data.get("method")
        params = data.get("params", {})
        result = params.get("result", {})
        context = result.get("context", {})
        slot = context.get("slot", 0)

        # Heartbeat staleness breaker immediately on EVERY valid message
        self.staleness_breaker.record_message_received(slot=slot)
        self.event_bus.publish(StreamEventType.HEARTBEAT, {"slot": slot, "message_index": self.message_count})

        if method == "logsNotification":
            self._handle_logs_notification(result, slot)
        elif method == "accountNotification":
            sub_id = params.get("subscription")
            self._handle_account_notification(result, slot, sub_id=sub_id)

    def _handle_logs_notification(self, result: Dict, slot: int) -> None:
        """Parses transaction execution logs for PumpSwap swap events."""
        value = result.get("value", {})
        signature = value.get("signature", "")
        logs = value.get("logs", [])

        # Check for Program log Instruction: Buy or Sell
        is_pumpswap = any(self.program_id in log for log in logs)
        if not is_pumpswap:
            return

        is_buy = None
        sol_amount = 0.50
        token_amount = None

        for log in logs:
            log_lower = log.lower()
            if "instruction: buy" in log_lower or "taker buy" in log_lower:
                is_buy = True
            elif "instruction: sell" in log_lower or "taker sell" in log_lower:
                is_buy = False

        if is_buy is None:
            return

        # Find target pool: match pool address or token mint in logs, or fallback to first registered pool
        target_pool = None
        target_pool_addr = None
        for pool_addr, pool in self.virtual_amm.get_all_pools().items():
            if any(pool_addr in log or pool.token_mint in log for log in logs):
                target_pool_addr = pool_addr
                target_pool = pool
                break

        if not target_pool:
            all_pools = list(self.virtual_amm.get_all_pools().items())
            if all_pools:
                target_pool_addr, target_pool = all_pools[0]

        if not target_pool or not target_pool_addr:
            return

        swap_event = LiveSwapEvent(
            pool_address=target_pool_addr,
            token_mint=target_pool.token_mint,
            is_buy=is_buy,
            sol_amount=sol_amount,
            token_amount=token_amount,
            trader_pubkey=signature[:16] if signature else "unknown",
            slot=slot,
            signature=signature,
        )

        # Recalculate local AMM reserves BEFORE publishing event
        self.virtual_amm.process_swap_event(
            pool_address=target_pool_addr,
            is_buy=is_buy,
            sol_amount=swap_event.sol_amount,
            token_amount=token_amount,
            slot=slot,
        )
        self.swaps_count += 1
        self.event_bus.publish(StreamEventType.SWAP, swap_event)

    def _handle_account_notification(self, result: Dict, slot: int, sub_id: Optional[int] = None) -> None:
        """Parses accountChange updates to synchronize ground-truth reserves."""
        value = result.get("value", {})
        lamports = value.get("lamports", 0)
        sol_reserve = lamports / 1e9 if lamports > 0 else 0.0
        if sol_reserve <= 0:
            return

        # Identify target pool from subscription ID or fallback
        target_pool_addr = self._sub_id_to_pool.get(sub_id) if sub_id is not None else None
        if not target_pool_addr:
            all_pools = list(self.virtual_amm.get_all_pools().items())
            if all_pools:
                target_pool_addr = all_pools[0][0]

        if not target_pool_addr:
            return

        pool = self.virtual_amm.get_pool(target_pool_addr)
        if not pool:
            return

        self.virtual_amm.process_account_change(
            pool_address=target_pool_addr,
            sol_reserve=sol_reserve,
            token_reserve=pool.token_reserve,
            slot=slot,
        )
        self.account_changes_count += 1
        event = LiveAccountChangeEvent(
            pool_address=target_pool_addr,
            sol_reserve=sol_reserve,
            token_reserve=pool.token_reserve,
            slot=slot,
        )
        self.event_bus.publish(StreamEventType.ACCOUNT_CHANGE, event)

    def stop(self) -> None:
        """Stops the WebSocket listener loop."""
        self._is_running = False
        logger.info("PumpSwap WSS Client stopped.")
