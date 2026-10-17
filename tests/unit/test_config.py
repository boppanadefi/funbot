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
    assert risk_cfg.min_age_minutes >= 3.0, "Invariant 1 & 3: Minimum cooldown must be >= 3m (bypassing protocol cliff)"
    assert risk_cfg.market_structure_mode == "VOLUME_CONSOLIDATION_STRUCTURAL_BREAK", "Invariant 3: Market structure mode"
    assert risk_cfg.consolidation_volume_threshold_pct <= 0.20, "Invariant 3: Consolidation volume exhaustion threshold <= 20%"
    assert risk_cfg.breakout_buy_volume_multiplier >= 1.50, "Invariant 3: Breakout buy volume multiplier >= 1.5x"
    assert risk_cfg.mode == "FRICTION_BUSTING_SCALE_OUT", "Invariant 2: Friction-Busting Scale-Out mandatory"
    assert risk_cfg.scale_out_mandatory_under_usd == 50.0, "Invariant 2: Path A+ prohibited for positions < $50"
    assert risk_cfg.tp1_size_pct == 0.90, "Invariant 2: TP1 must sell 90% of position"
    assert 0.08 <= risk_cfg.tp1_target_gross_pct <= 0.10, "Invariant 2: TP1 target must be +8% to +10% gross"
    assert risk_cfg.runner_size_pct == 0.10, "Invariant 2: Lottery runner must retain 10%"
    assert risk_cfg.runner_hard_kill_timeout_minutes == 3.0, "Fix 5: Runner hard time kill timeout = 3.0m"
    assert risk_cfg.runner_target_gross_pct == 0.30, "Fix 5: Runner target hurdle = +30% gross"
    assert risk_cfg.total_swaps == 3, "Invariant 2: 1 entry + up to 2 exit legs (TP1 + Runner)"
    assert risk_cfg.regime_a_max_expected_cost <= 7.0, "Invariant 4: Pre-flight cost cap <= 7%"
    assert risk_cfg.min_lifetime_txs == 3, "Invariant 5: Fresh burner threshold"
    assert risk_cfg.daily_max_realized_loss_usd == 10.0, "Invariant 7: $10 max daily loss"
    assert risk_cfg.max_holding_timeout_minutes == 20.0, "Invariant 7: 20-minute holding timeout"
    # Verify Fix 3: Local-Structure & Time-Decay Exits
    assert risk_cfg.structure_trailing_lookback_candles == 2, "Fix 3: Structure lookback = 2 candles"
    assert risk_cfg.momentum_decay_stagnant_candles == 2, "Fix 3: Momentum decay stagnant candles = 2"
    assert risk_cfg.momentum_decay_volume_drop_pct == 0.40, "Fix 3: Volume drop threshold = 40%"
    assert risk_cfg.liquidity_collapse_slippage_pct == 0.05, "Fix 3: Liquidity collapse slippage = 5%"
    assert risk_cfg.liquidity_check_interval_seconds == 30.0, "Fix 3: Liquidity check interval = 30s"
    # Verify Fix 4: Real-Time Order Flow Dynamics
    assert risk_cfg.order_flow_mode == "REAL_TIME_ORDER_FLOW_DYNAMICS", "Fix 4: Order flow mode"
    assert risk_cfg.min_vuw_ratio >= 1.20, "Fix 4: Minimum VUW ratio >= 1.20"
    assert risk_cfg.min_aggression_ratio >= 2.00, "Fix 4: Minimum Aggression ratio >= 2.00"
    # Verify Fix 5: Dynamic Liquidity-Based Sizing
    assert risk_cfg.dynamic_sizing_enabled is True, "Fix 5: Dynamic sizing enabled"
    assert risk_cfg.max_acceptable_impact_pct == 1.50, "Fix 5: Target 1.5% max one-way impact"
    assert risk_cfg.min_position_usd == 3.00, "Fix 5: $3.00 minimum viable swap floor"
    assert risk_cfg.max_position_usd == 25.00, "Fix 5: $25.00 active wallet limit ceiling"

    # Verify Telemetry Config
    assert tele_cfg.required_observation_days == 14
    assert tele_cfg.min_candidates_per_day == 3.0


def test_invariant_2_path_a_prohibited_for_under_50(tmp_path, monkeypatch):
    """Verifies that Path A+ is strictly prohibited for positions < $50."""
    loader = ConfigLoader()
    base_dir = loader.base_dir

    # Mock yaml with PATH_A_PLUS and <$50 position
    bad_yaml = """
timing:
  min_age_minutes: 8.0
  max_age_minutes: 25.0
  buyback_cliff_window: [5.0, 8.0]
  max_holding_timeout_minutes: 20.0
exit_strategy:
  mode: "PATH_A_PLUS"
  scale_out_mandatory_under_usd: 50.0
  prohibit_tranche_exits: true
  total_swaps: 2
  initial_profit_hurdle_regime_a: 0.18
  initial_profit_hurdle_regime_b: 0.14
  ratchet_net_lock_min: 0.08
  ratchet_net_lock_max: 0.10
  trailing_stop_pct: 0.12
preflight_gate:
  regime_a:
    max_expected_cost_pct: 7.00
    min_gross_profit_hurdle_pct: 18.00
    max_stress_cost_pct: 10.00
  regime_b:
    max_expected_cost_pct: 6.00
    min_gross_profit_hurdle_pct: 14.00
    max_stress_cost_pct: 10.00
cluster_risk:
  min_pretrade_lifetime_txs: 3
  max_common_parent_funding_pct: 30.0
execution:
  target_position_usd: 15.00
bankroll:
  daily_max_realized_loss_usd: 10.00
  max_trades_per_day: 5
"""
    config_file = tmp_path / "risk_limits.yaml"
    config_file.write_text(bad_yaml, encoding="utf-8")

    monkeypatch.setattr(loader, "base_dir", tmp_path.parent)
    cfg_dir = tmp_path.parent / "config"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "risk_limits.yaml").write_text(bad_yaml, encoding="utf-8")

    with pytest.raises(InvariantViolationError, match="Invariant 2: Path A\\+ is prohibited for positions < \\$50"):
        loader.load_risk_limits()

