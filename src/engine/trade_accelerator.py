"""
Phase 4: Trade Accelerator Configuration & Concurrency Override.
Temporarily relaxes constraints during the 24-hour smoke test to force trades through the pipeline:
- Concurrency Override: Up to 3 concurrent positions (instead of 1).
- Filter Relaxation: Lower VUW threshold to 1.05 and Baseline Demand Floor to $50/min.
- Position Size Floor: Drop minimum dynamic size to $2.00 to test AMM math on tiny pools.
- Ledger Tagging: All trades during this session tagged as REGIME_TEST.
"""

from dataclasses import dataclass
from typing import Dict, Any


@dataclass
class TradeAcceleratorConfig:
    """Trade Accelerator settings for 24-Hour Infrastructure & Execution Smoke Test."""
    enabled: bool = False
    concurrency_limit: int = 3                # Up to 3 concurrent positions (standard is 1)
    relaxed_vuw_threshold: float = 1.05       # Lowered from 1.20 to force volume
    relaxed_baseline_demand_usd: float = 50.0 # Lowered from $100.0/min
    relaxed_min_dynamic_size_usd: float = 2.00# Lowered from $3.00 to test tiny pools
    session_regime_tag: str = "REGIME_TEST"   # Explicit tag; P&L excluded from strategy viability

    def to_dict(self) -> Dict[str, Any]:
        return {
            "enabled": self.enabled,
            "concurrency_limit": self.concurrency_limit,
            "relaxed_vuw_threshold": self.relaxed_vuw_threshold,
            "relaxed_baseline_demand_usd": self.relaxed_baseline_demand_usd,
            "relaxed_min_dynamic_size_usd": self.relaxed_min_dynamic_size_usd,
            "session_regime_tag": self.session_regime_tag,
        }
