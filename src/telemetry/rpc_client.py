"""
Dedicated Solana RPC & WebSocket Connection Health Monitor.
Probes Helius / Solana Mainnet endpoints for:
1. HTTP RPC Health & Latency (getSlot, getHealth)
2. WebSocket Handshake & Responsiveness (logsSubscribe on PumpSwap)
3. Latency Circuit Breaker compliance (<1000ms threshold)
"""

import asyncio
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import time
from typing import Any, Dict, Optional
import requests
import websockets

from src.utils.config import ConfigLoader

logger = logging.getLogger("SolanaRPCClient")


def mask_endpoint_key(url: str) -> str:
    """Masks secret API key in URLs for safe UI display (e.g., api-key=795a...125e)."""
    if "api-key=" in url:
        prefix, key_part = url.split("api-key=", 1)
        # Handle trailing params if any
        if "&" in key_part:
            key, rest = key_part.split("&", 1)
            masked = key[:4] + "..." + key[-4:] if len(key) > 8 else "****"
            return f"{prefix}api-key={masked}&{rest}"
        else:
            masked = key_part[:4] + "..." + key_part[-4:] if len(key_part) > 8 else "****"
            return f"{prefix}api-key={masked}"
    return url


class SolanaConnectionMonitor:
    """
    Monitors, probes, and diagnostics for dedicated Solana RPC and WebSocket connections.
    """

    DEFAULT_PUMPSWAP_PROGRAM = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"

    _cached_status: Optional[Dict[str, Any]] = None
    _last_check_ts: float = 0.0
    CACHE_TTL_SECONDS: float = 15.0

    def __init__(self, http_url: Optional[str] = None, ws_url: Optional[str] = None):
        cfg = ConfigLoader().load_telemetry_config()
        self.http_url = http_url or cfg.http_rpc_url
        self.ws_url = ws_url or cfg.ws_rpc_url
        self.program_id = self.DEFAULT_PUMPSWAP_PROGRAM

    def check_http_rpc(self, timeout_sec: float = 4.0) -> Dict[str, Any]:
        """Probes the Solana HTTP RPC endpoint for current slot and latency."""
        t0 = time.time()
        try:
            resp = requests.post(
                self.http_url,
                json={"jsonrpc": "2.0", "id": 1, "method": "getSlot"},
                timeout=timeout_sec,
            )
            latency_ms = (time.time() - t0) * 1000.0
            if resp.status_code == 200:
                data = resp.json()
                slot = data.get("result")
                return {
                    "ok": True,
                    "slot": slot,
                    "latency_ms": round(latency_ms, 1),
                    "endpoint": mask_endpoint_key(self.http_url),
                    "status": "ONLINE",
                    "error": None,
                }
            return {
                "ok": False,
                "slot": None,
                "latency_ms": round(latency_ms, 1),
                "endpoint": mask_endpoint_key(self.http_url),
                "status": f"HTTP {resp.status_code}",
                "error": resp.text[:100],
            }
        except Exception as e:
            latency_ms = (time.time() - t0) * 1000.0
            return {
                "ok": False,
                "slot": None,
                "latency_ms": round(latency_ms, 1),
                "endpoint": mask_endpoint_key(self.http_url),
                "status": "OFFLINE",
                "error": str(e),
            }

    async def _probe_ws_async(self, timeout_sec: float = 3.5) -> Dict[str, Any]:
        """Asynchronously tests the WebSocket endpoint handshake."""
        t0 = time.time()
        try:
            async with asyncio.timeout(timeout_sec):
                async with websockets.connect(self.ws_url) as ws:
                    # Send logsSubscribe ping
                    req = {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "logsSubscribe",
                        "params": [
                            {"mentions": [self.program_id]},
                            {"commitment": "processed"},
                        ],
                    }
                    await ws.send(json.dumps(req))
                    resp_str = await ws.recv()
                    latency_ms = (time.time() - t0) * 1000.0
                    data = json.loads(resp_str)
                    sub_id = data.get("result")
                    return {
                        "ok": True,
                        "sub_id": sub_id,
                        "latency_ms": round(latency_ms, 1),
                        "endpoint": mask_endpoint_key(self.ws_url),
                        "status": "CONNECTED",
                        "error": None,
                    }
        except Exception as e:
            latency_ms = (time.time() - t0) * 1000.0
            return {
                "ok": False,
                "sub_id": None,
                "latency_ms": round(latency_ms, 1),
                "endpoint": mask_endpoint_key(self.ws_url),
                "status": "DISCONNECTED",
                "error": str(e),
            }

    def check_ws_rpc(self, timeout_sec: float = 3.5) -> Dict[str, Any]:
        """Synchronous wrapper to probe the WebSocket endpoint."""
        try:
            return asyncio.run(self._probe_ws_async(timeout_sec=timeout_sec))
        except Exception as e:
            return {
                "ok": False,
                "sub_id": None,
                "latency_ms": 0.0,
                "endpoint": mask_endpoint_key(self.ws_url),
                "status": "DISCONNECTED",
                "error": str(e),
            }

    def get_full_diagnostics(self, force_refresh: bool = False) -> Dict[str, Any]:
        """
        Returns full connection diagnostic state across HTTP RPC, WSS, and staleness breaker.
        Caches for CACHE_TTL_SECONDS unless force_refresh is True.
        """
        now = time.time()
        if (
            not force_refresh
            and SolanaConnectionMonitor._cached_status is not None
            and (now - SolanaConnectionMonitor._last_check_ts) < self.CACHE_TTL_SECONDS
        ):
            return SolanaConnectionMonitor._cached_status

        http_diag = self.check_http_rpc()
        # If HTTP worked, we know network is up, probe WS
        ws_diag = self.check_ws_rpc() if http_diag["ok"] else {
            "ok": False,
            "status": "SKIPPED",
            "latency_ms": 0.0,
            "endpoint": mask_endpoint_key(self.ws_url),
            "error": "HTTP RPC unreachable",
        }

        is_helius = "helius" in self.http_url.lower() or "helius" in self.ws_url.lower()
        provider_name = "Helius Dedicated RPC (Mainnet)" if is_helius else "Solana Public Cluster"

        is_connected = http_diag["ok"] and ws_diag["ok"]
        avg_latency = (
            (http_diag["latency_ms"] + ws_diag.get("latency_ms", 0.0)) / 2.0
            if ws_diag.get("latency_ms", 0.0) > 0
            else http_diag["latency_ms"]
        )

        diag = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "provider": provider_name,
            "is_connected": is_connected,
            "http": http_diag,
            "ws": ws_diag,
            "avg_latency_ms": round(avg_latency, 1),
            "slot": http_diag.get("slot"),
            "staleness_compliant": avg_latency < 1000.0,
            "program_id": self.program_id,
        }

        SolanaConnectionMonitor._cached_status = diag
        SolanaConnectionMonitor._last_check_ts = now
        return diag
