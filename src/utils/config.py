"""
Configuration loader and invariant validator for Funbot.
Ensures all 7 Invariant System Mitigations are strictly enforced at runtime.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional
import yaml


class InvariantViolationError(ValueError):
    """Raised when configuration violates any of the 7 Invariant System Mitigations."""
    pass


@dataclass
class FeeTier:
    tier_id: int
    name: str
    venue_fee_pct: float
    max_market_cap_sol: float = float("inf")
    min_market_cap_sol: float = 0.0


@dataclass
class FeeSchedule:
    quote_asset: str
    tiers: List[FeeTier]
    base_signature_fee_sol: float
    priority_fee_cap_sol: float
    p50_jito_tip_sol: float
    p75_jito_tip_sol: float
    max_entry_tip_cap_pct: float
    emergency_exit_budget_sol: float


@dataclass
class RiskLimits:
    min_age_minutes: float
    max_age_minutes: float
    buyback_cliff_window: List[float]
    max_holding_timeout_minutes: float
    mode: str
    prohibit_tranche_exits: bool
    total_swaps: int
    scale_out_mandatory_under_usd: float
    tp1_size_pct: float
    tp1_target_gross_pct: float
    runner_size_pct: float
    runner_trailing_pct: float
    protective_stop_loss_pct: float
    # Fix 5: Runner Hard Time Kill (<+30% within 3m of TP1)
    runner_hard_kill_timeout_minutes: float
    runner_target_gross_pct: float
    # Fix 3: Local-Structure & Time-Decay Exits
    structure_trailing_lookback_candles: int
    momentum_decay_stagnant_candles: int
    momentum_decay_volume_drop_pct: float
    liquidity_collapse_slippage_pct: float
    liquidity_check_interval_seconds: float
    # Invariant 3: Volume Consolidation + Structural Breakout
    market_structure_mode: str
    min_cooldown_minutes: float
    spike_window_minutes: float
    consolidation_volume_threshold_pct: float
    breakout_buy_volume_multiplier: float
    initial_profit_hurdle_regime_a: float
    initial_profit_hurdle_regime_b: float
    ratchet_net_lock_min: float
    ratchet_net_lock_max: float
    trailing_stop_pct: float
    regime_a_max_expected_cost: float
    regime_b_max_expected_cost: float
    regime_a_hurdle: float
    regime_b_hurdle: float
    max_stress_cost: float
    min_lifetime_txs: int
    max_common_parent_pct: float
    # Fix 4: Real-Time Order Flow Dynamics
    order_flow_mode: str = "REAL_TIME_ORDER_FLOW_DYNAMICS"
    min_vuw_ratio: float = 1.20
    min_aggression_ratio: float = 2.00
    # Fix 5: Dynamic Liquidity-Based Sizing
    dynamic_sizing_enabled: bool = True
    max_acceptable_impact_pct: float = 1.50
    min_position_usd: float = 3.00
    max_position_usd: float = 25.00
    daily_max_realized_loss_usd: float = 10.00
    max_trades_per_day: int = 5
    target_position_usd: float = 15.00


@dataclass
class TelemetryConfig:
    cluster: str
    http_rpc_url: str
    ws_rpc_url: str
    pumpswap_program_id: str
    rolling_windows_seconds: List[int]
    heartbeat_interval_seconds: float
    reconnect_delay_seconds: float
    reserve_snapshot_interval_seconds: float
    required_observation_days: int
    min_candidates_per_day: float
    min_depth_pass_rate_pct: float
    max_acceptable_friction_pct: float
    simulated_test_notional_usd: float
    sol_usd_reference_price: float
    sqlite_db_path: str
    parquet_export_dir: str
    gate_report_path: str


class ConfigLoader:
    def __init__(self, base_dir: Optional[Path] = None):
        self.base_dir = base_dir or Path(__file__).resolve().parent.parent.parent

    def load_fee_schedule(self) -> FeeSchedule:
        path = self.base_dir / "config" / "fee_schedule.yaml"
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)

        tiers = [
            FeeTier(
                tier_id=t["tier_id"],
                name=t["name"],
                venue_fee_pct=float(t["venue_fee_pct"]),
                max_market_cap_sol=float(t.get("max_market_cap_sol", float("inf"))),
                min_market_cap_sol=float(t.get("min_market_cap_sol", 0.0)),
            )
            for t in data["tiers"]
        ]

        net = data["network_overheads"]
        return FeeSchedule(
            quote_asset=data["quote_asset"],
            tiers=tiers,
            base_signature_fee_sol=float(net["base_signature_fee_sol"]),
            priority_fee_cap_sol=float(net["priority_fee_cap_sol"]),
            p50_jito_tip_sol=float(net["p50_jito_tip_sol"]),
            p75_jito_tip_sol=float(net["p75_jito_tip_sol"]),
            max_entry_tip_cap_pct=float(net["max_entry_tip_cap_pct"]),
            emergency_exit_budget_sol=float(net["emergency_exit_budget_sol"]),
        )

    def load_risk_limits(self) -> RiskLimits:
        path = self.base_dir / "config" / "risk_limits.yaml"
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)

        timing = data["timing"]
        exit_strat = data["exit_strategy"]
        gate = data["preflight_gate"]
        cluster = data["cluster_risk"]
        bankroll = data["bankroll"]
        exec_cfg = data["execution"]

        ms_cfg = data.get("market_structure", {})

        # Validate Invariants
        # Invariant 1 & 3: Minimum 3-minute cooldown to avoid initial protocol buyback cliff (minutes 0-3)
        if timing["min_age_minutes"] < 3.0:
            raise InvariantViolationError("Invariant 1 & 3: min_age_minutes must be >= 3.0 to bypass initial protocol buyback cliff")

        mode = exit_strat.get("mode", "FRICTION_BUSTING_SCALE_OUT")
        target_pos_usd = float(exec_cfg.get("target_position_usd", 15.0))
        scale_out_cutoff = float(exit_strat.get("scale_out_mandatory_under_usd", 50.0))

        # Invariant 2: Path A+ is prohibited for positions < $50. Friction-Busting Scale-Out mandatory.
        if mode == "PATH_A_PLUS" and target_pos_usd < scale_out_cutoff:
            raise InvariantViolationError(
                "Invariant 2: Path A+ is prohibited for positions < $50. Friction-Busting Scale-Out mandatory."
            )

        if mode == "FRICTION_BUSTING_SCALE_OUT":
            tp1_size = float(exit_strat.get("tp1_size_pct", 0.90))
            runner_size = float(exit_strat.get("runner_size_pct", 0.10))
            tp1_target = float(exit_strat.get("tp1_target_gross_pct", 0.09))
            if round(tp1_size + runner_size, 4) != 1.00:
                raise InvariantViolationError("Invariant 2: TP1 + Runner must equal 100%")
            if not (0.08 <= tp1_target <= 0.10):
                raise InvariantViolationError("Invariant 2: TP1 target gross must be between +8% and +10%")

        # Invariant 3: Volume Exhaustion Consolidation (<20% peak 1m vol) + Structural Breakout (>1.5x avg buy vol)
        consolidation_vol_thresh = float(ms_cfg.get("consolidation_volume_threshold_pct", 0.20))
        if consolidation_vol_thresh > 0.20:
            raise InvariantViolationError("Invariant 3: consolidation_volume_threshold_pct must be <= 0.20 (20% exhaustion threshold)")

        breakout_mult = float(ms_cfg.get("breakout_buy_volume_multiplier", 1.50))
        if breakout_mult < 1.50:
            raise InvariantViolationError("Invariant 3: breakout_buy_volume_multiplier must be >= 1.50 (1.5x consolidation average)")

        # Fix 3: Local-Structure & Time-Decay Exits
        struct_lookback = int(exit_strat.get("structure_trailing_lookback_candles", 2))
        if struct_lookback < 2:
            raise InvariantViolationError("Fix 3: structure_trailing_lookback_candles must be >= 2")

        mom_stagnant = int(exit_strat.get("momentum_decay_stagnant_candles", 2))
        if mom_stagnant < 2:
            raise InvariantViolationError("Fix 3: momentum_decay_stagnant_candles must be >= 2")

        mom_vol_drop = float(exit_strat.get("momentum_decay_volume_drop_pct", 0.40))
        if not (0.0 < mom_vol_drop <= 0.60):
            raise InvariantViolationError("Fix 3: momentum_decay_volume_drop_pct must be between 0 and 0.60")

        liq_slip = float(exit_strat.get("liquidity_collapse_slippage_pct", 0.05))
        if not (0.01 <= liq_slip <= 0.10):
            raise InvariantViolationError("Fix 3: liquidity_collapse_slippage_pct must be between 0.01 and 0.10")

        liq_interval = float(exit_strat.get("liquidity_check_interval_seconds", 30.0))

        # Fix 4: Real-Time Order Flow Dynamics
        of_cfg = data.get("order_flow_dynamics", {})
        min_vuw = float(of_cfg.get("min_vuw_ratio", 1.20))
        if min_vuw < 1.20:
            raise InvariantViolationError("Invariant 5 (Fix 4): min_vuw_ratio must be >= 1.20 (Velocity of Unique Wallets floor)")

        min_aggression = float(of_cfg.get("min_aggression_ratio", 2.00))
        if min_aggression < 2.00:
            raise InvariantViolationError("Invariant 5 (Fix 4): min_aggression_ratio must be >= 2.00 (Aggression ratio floor)")

        # Fix 5: Dynamic Liquidity-Based Sizing
        dyn_enabled = bool(exec_cfg.get("dynamic_sizing_enabled", True))
        max_acceptable_impact = float(exec_cfg.get("max_acceptable_impact_pct", 1.50))
        if max_acceptable_impact > 2.00:
            raise InvariantViolationError("Fix 5: max_acceptable_impact_pct cannot exceed 2.00% (target 1.50%)")

        min_pos_usd = float(exec_cfg.get("min_position_usd", 3.00))
        if min_pos_usd < 3.00:
            raise InvariantViolationError("Fix 5: min_position_usd must be >= $3.00 to cover Solana tx overhead")

        max_pos_usd = float(exec_cfg.get("max_position_usd", 25.00))
        if max_pos_usd > 25.00:
            raise InvariantViolationError("Fix 5: max_position_usd cannot exceed $25.00 active wallet limit")

        if gate["regime_a"]["max_expected_cost_pct"] > 7.0:
            raise InvariantViolationError("Invariant 4: Regime A expected cost cap cannot exceed 7.0%")

        return RiskLimits(
            min_age_minutes=float(timing["min_age_minutes"]),
            max_age_minutes=float(timing["max_age_minutes"]),
            buyback_cliff_window=[float(x) for x in timing["buyback_cliff_window"]],
            max_holding_timeout_minutes=float(timing["max_holding_timeout_minutes"]),
            mode=exit_strat["mode"],
            prohibit_tranche_exits=bool(exit_strat.get("prohibit_tranche_exits", False)),
            total_swaps=int(exit_strat.get("total_swaps", 3)),
            scale_out_mandatory_under_usd=scale_out_cutoff,
            tp1_size_pct=float(exit_strat.get("tp1_size_pct", 0.90)),
            tp1_target_gross_pct=float(exit_strat.get("tp1_target_gross_pct", 0.09)),
            runner_size_pct=float(exit_strat.get("runner_size_pct", 0.10)),
            runner_trailing_pct=float(exit_strat.get("runner_trailing_pct", 0.15)),
            protective_stop_loss_pct=float(exit_strat.get("protective_stop_loss_pct", 0.10)),
            runner_hard_kill_timeout_minutes=float(exit_strat.get("runner_hard_kill_timeout_minutes", 3.0)),
            runner_target_gross_pct=float(exit_strat.get("runner_target_gross_pct", 0.30)),
            structure_trailing_lookback_candles=struct_lookback,
            momentum_decay_stagnant_candles=mom_stagnant,
            momentum_decay_volume_drop_pct=mom_vol_drop,
            liquidity_collapse_slippage_pct=liq_slip,
            liquidity_check_interval_seconds=liq_interval,
            market_structure_mode=ms_cfg.get("mode", "VOLUME_CONSOLIDATION_STRUCTURAL_BREAK"),
            min_cooldown_minutes=float(ms_cfg.get("min_cooldown_minutes", 3.0)),
            spike_window_minutes=float(ms_cfg.get("spike_window_minutes", 3.0)),
            consolidation_volume_threshold_pct=consolidation_vol_thresh,
            breakout_buy_volume_multiplier=breakout_mult,
            initial_profit_hurdle_regime_a=float(exit_strat["initial_profit_hurdle_regime_a"]),
            initial_profit_hurdle_regime_b=float(exit_strat["initial_profit_hurdle_regime_b"]),
            ratchet_net_lock_min=float(exit_strat["ratchet_net_lock_min"]),
            ratchet_net_lock_max=float(exit_strat["ratchet_net_lock_max"]),
            trailing_stop_pct=float(exit_strat.get("trailing_stop_pct", 0.15)),
            regime_a_max_expected_cost=float(gate["regime_a"]["max_expected_cost_pct"]),
            regime_b_max_expected_cost=float(gate["regime_b"]["max_expected_cost_pct"]),
            regime_a_hurdle=float(gate["regime_a"]["min_gross_profit_hurdle_pct"]),
            regime_b_hurdle=float(gate["regime_b"]["min_gross_profit_hurdle_pct"]),
            max_stress_cost=float(gate["regime_a"]["max_stress_cost_pct"]),
            min_lifetime_txs=int(cluster.get("min_pretrade_lifetime_txs", 3)),
            max_common_parent_pct=float(cluster.get("max_common_parent_funding_pct", 30.0)),
            order_flow_mode=of_cfg.get("mode", "REAL_TIME_ORDER_FLOW_DYNAMICS"),
            min_vuw_ratio=min_vuw,
            min_aggression_ratio=min_aggression,
            dynamic_sizing_enabled=dyn_enabled,
            max_acceptable_impact_pct=max_acceptable_impact,
            min_position_usd=min_pos_usd,
            max_position_usd=max_pos_usd,
            daily_max_realized_loss_usd=float(bankroll["daily_max_realized_loss_usd"]),
            max_trades_per_day=int(bankroll["max_trades_per_day"]),
            target_position_usd=float(exec_cfg["target_position_usd"]),
        )

    def load_telemetry_config(self) -> TelemetryConfig:
        path = self.base_dir / "config" / "telemetry_config.yaml"
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)

        net = data["network"]
        sampling = data["sampling"]
        gate = data["falsification_gate"]
        storage = data["storage"]

        return TelemetryConfig(
            cluster=net["cluster"],
            http_rpc_url=net["http_rpc_url"],
            ws_rpc_url=net["ws_rpc_url"],
            pumpswap_program_id=net["pumpswap_program_id"],
            rolling_windows_seconds=[int(x) for x in sampling["rolling_windows_seconds"]],
            heartbeat_interval_seconds=float(sampling["heartbeat_interval_seconds"]),
            reconnect_delay_seconds=float(sampling["reconnect_delay_seconds"]),
            reserve_snapshot_interval_seconds=float(sampling["reserve_snapshot_interval_seconds"]),
            required_observation_days=int(gate["required_observation_days"]),
            min_candidates_per_day=float(gate["min_candidates_per_day"]),
            min_depth_pass_rate_pct=float(gate["min_depth_pass_rate_pct"]),
            max_acceptable_friction_pct=float(gate["max_acceptable_friction_pct"]),
            simulated_test_notional_usd=float(gate["simulated_test_notional_usd"]),
            sol_usd_reference_price=float(gate["sol_usd_reference_price"]),
            sqlite_db_path=str(self.base_dir / storage["sqlite_db_path"]),
            parquet_export_dir=str(self.base_dir / storage["parquet_export_dir"]),
            gate_report_path=str(self.base_dir / storage["gate_report_path"]),
        )
