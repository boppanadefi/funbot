"""
Paper Trading Simulation & Out-of-Sample Validation Pipeline.
Enforces Guardrail 2 (Path A+ 100% Dynamic Trailing Single Exit) and Guardrail 7 (Risk Capacity).
"""

from src.engine.models import (
    PaperPosition,
    PositionState,
    ExecutionLeg,
    TradeOutcome,
    ValidationReport,
)
from src.engine.paper_ledger import PaperLedger
from src.engine.simulator import PathASimulator
from src.engine.risk_monitor import DailyRiskMonitor
from src.engine.validator import OutOfSampleValidator
from src.engine.hit_rate_surface import HitRateSurfaceAnalyzer

__all__ = [
    "PaperPosition",
    "PositionState",
    "ExecutionLeg",
    "TradeOutcome",
    "ValidationReport",
    "PaperLedger",
    "PathASimulator",
    "DailyRiskMonitor",
    "OutOfSampleValidator",
    "HitRateSurfaceAnalyzer",
]
