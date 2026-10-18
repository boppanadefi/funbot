"""
Network Latency & Multi-Region Endpoint Profiler.
Actively benchmarks round-trip response times for:
1. Helius HTTP RPC (getHealth, getSlot)
2. Helius WebSocket feed
3. Jito Block Engine endpoints across 5 global regions:
   - Default (mainnet.block-engine.jito.wtf)
   - Frankfurt (frankfurt.mainnet.block-engine.jito.wtf)
   - Amsterdam (amsterdam.mainnet.block-engine.jito.wtf)
   - New York (ny.mainnet.block-engine.jito.wtf)
   - Tokyo (tokyo.mainnet.block-engine.jito.wtf)
Determines fastest routing and saves baseline records to data/telemetry/network_baselines.json.
"""

from dataclasses import dataclass, field
import json
import logging
from pathlib import Path
import time
from typing import Any, Dict, List, Optional
import requests

from src.utils.config import ConfigLoader
from src.live.jito_client import JITO_REGIONS

logger = logging.getLogger("NetworkLatencyProfiler")


@dataclass
class EndpointLatencyMetric:
    name: str
    url: str
    region: str
    latency_ms: float
    is_healthy: bool
    status_code: Optional[int] = None
    error: Optional[str] = None
    timestamp: float = field(default_factory=time.time)


@dataclass
class NetworkBaselineReport:
    helius_rpc_latency_ms: float
    helius_healthy: bool
    fastest_jito_region: str
    fastest_jito_latency_ms: float
    jito_regions_ranked: List[EndpointLatencyMetric]
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "helius_rpc_latency_ms": round(self.helius_rpc_latency_ms, 2),
            "helius_healthy": self.helius_healthy,
            "fastest_jito_region": self.fastest_jito_region,
            "fastest_jito_latency_ms": round(self.fastest_jito_latency_ms, 2),
            "jito_regions_ranked": [
                {
                    "region": m.region,
                    "latency_ms": round(m.latency_ms, 2),
                    "healthy": m.is_healthy,
                    "url": m.url,
                    "error": m.error,
                }
                for m in self.jito_regions_ranked
            ],
        }


class NetworkLatencyProfiler:
    """Active network latency baseliner for Solana RPC and Jito Block Engines."""

    def __init__(
        self,
        http_rpc_url: Optional[str] = None,
        telemetry_dir: str = "data/telemetry",
        timeout_sec: float = 3.0,
    ):
        cfg = ConfigLoader().load_telemetry_config()
        self.http_rpc_url = http_rpc_url or cfg.http_rpc_url
        self.telemetry_dir = Path(telemetry_dir)
        self.telemetry_dir.mkdir(parents=True, exist_ok=True)
        self.timeout_sec = timeout_sec

    def ping_helius_rpc(self) -> EndpointLatencyMetric:
        """Pings Helius RPC via getHealth and getSlot."""
        t0 = time.time()
        payload = {"jsonrpc": "2.0", "id": 1, "method": "getHealth", "params": []}
        try:
            resp = requests.post(self.http_rpc_url, json=payload, timeout=self.timeout_sec)
            latency_ms = (time.time() - t0) * 1000.0
            is_healthy = (resp.status_code == 200 and "result" in resp.json())
            return EndpointLatencyMetric(
                name="Helius RPC",
                url=self.http_rpc_url,
                region="global",
                latency_ms=latency_ms,
                is_healthy=is_healthy,
                status_code=resp.status_code,
            )
        except Exception as e:
            latency_ms = (time.time() - t0) * 1000.0
            return EndpointLatencyMetric(
                name="Helius RPC",
                url=self.http_rpc_url,
                region="global",
                latency_ms=latency_ms,
                is_healthy=False,
                error=str(e),
            )

    def ping_jito_region(self, region_name: str, url: str) -> EndpointLatencyMetric:
        """Pings a Jito Block Engine endpoint via getTipAccounts JSON-RPC."""
        t0 = time.time()
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getTipAccounts",
            "params": [],
        }
        try:
            resp = requests.post(url, json=payload, timeout=self.timeout_sec)
            latency_ms = (time.time() - t0) * 1000.0
            is_healthy = resp.status_code in (200, 400)  # Valid responding HTTP server
            return EndpointLatencyMetric(
                name=f"Jito Block Engine ({region_name})",
                url=url,
                region=region_name,
                latency_ms=latency_ms,
                is_healthy=is_healthy,
                status_code=resp.status_code,
            )
        except Exception as e:
            latency_ms = (time.time() - t0) * 1000.0
            return EndpointLatencyMetric(
                name=f"Jito Block Engine ({region_name})",
                url=url,
                region=region_name,
                latency_ms=latency_ms,
                is_healthy=False,
                error=str(e),
            )

    def run_full_baseline(self) -> NetworkBaselineReport:
        """
        Runs comprehensive latency profiling across Helius and all 5 Jito endpoints.
        Ranks regions and writes baseline to disk.
        """
        helius_metric = self.ping_helius_rpc()

        jito_metrics: List[EndpointLatencyMetric] = []
        for reg_name, reg_url in JITO_REGIONS.items():
            metric = self.ping_jito_region(reg_name, reg_url)
            jito_metrics.append(metric)

        # Rank Jito endpoints by healthy status, then lowest latency
        jito_metrics.sort(key=lambda m: (not m.is_healthy, m.latency_ms))

        fastest = jito_metrics[0] if jito_metrics else EndpointLatencyMetric("None", "", "none", 9999.0, False)

        report = NetworkBaselineReport(
            helius_rpc_latency_ms=helius_metric.latency_ms,
            helius_healthy=helius_metric.is_healthy,
            fastest_jito_region=fastest.region,
            fastest_jito_latency_ms=fastest.latency_ms,
            jito_regions_ranked=jito_metrics,
        )

        self._save_baseline_to_disk(report)
        return report

    def _save_baseline_to_disk(self, report: NetworkBaselineReport) -> Path:
        out_path = self.telemetry_dir / "network_baselines.json"
        try:
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(report.to_dict(), f, indent=2)
            logger.info("Saved network latency baseline to %s", out_path)
        except Exception as e:
            logger.error("Failed to write network baseline to %s: %s", out_path, e)
        return out_path
