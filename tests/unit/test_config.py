"""
Unit tests for configuration loader and invariant validations.
"""

import pytest
from pathlib import Path
from src.utils.config import ConfigLoader, InvariantViolationError


def test_valid_config_loading():
    loader = ConfigLoader()
    fee_cfg = loader.load_fee_schedule()
    risk_cfg = loader.load_risk_limits()
    tele_cfg = loader.load_telemetry_config()

    # Verify Fee Schedule
    assert fee_cfg.quote_asset == "SOL"
    assert len(fee_cfg.tiers) == 3
    assert fee_cfg.tiers[0].venue_fee_pct == 1.250
    assert fee_cfg.tiers[2].venue_fee_pct == 0.300
    assert fee_cfg.max_entry_tip_cap_pct == 1.50
    assert fee_cfg.emergency_exit_budget_sol == 0.005

    # Verify Invariants in Risk Limits
    assert risk_cfg.min_age_minutes >= 8.0, "Invariant 1 & 3: Stabilization window must be >= 8m"
    assert risk_cfg.prohibit_tranche_exits is True, "Invariant 2: Path A+ requires tranche exits prohibited"
    assert risk_cfg.total_swaps == 2, "Invariant 2: Exactly 2 swaps total"
    assert risk_cfg.regime_a_max_expected_cost <= 7.0, "Invariant 4: Pre-flight cost cap <= 7%"
    assert risk_cfg.min_lifetime_txs == 3, "Invariant 5: Fresh burner threshold"
    assert risk_cfg.daily_max_realized_loss_usd == 10.0, "Invariant 7: $10 max daily loss"
    assert risk_cfg.max_holding_timeout_minutes == 20.0, "Invariant 7: 20-minute holding timeout"

    # Verify Telemetry Config
    assert tele_cfg.required_observation_days == 14
    assert tele_cfg.min_candidates_per_day == 3.0
