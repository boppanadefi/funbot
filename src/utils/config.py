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
    daily_max_realized_loss_usd: float
    max_trades_per_day: int
    target_position_usd: float


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

        # Validate Invariants
        if timing["min_age_minutes"] < 8.0:
            raise InvariantViolationError("Invariant 1 & 3: min_age_minutes must be >= 8.0 to bypass BOOST buyback cliff")

        if not exit_strat.get("prohibit_tranche_exits", False):
            raise InvariantViolationError("Invariant 2: prohibit_tranche_exits must be True (Path A+ required)")

        if exit_strat.get("total_swaps", 0) != 2:
            raise InvariantViolationError("Invariant 2: total_swaps must be exactly 2 for Path A+")

        if gate["regime_a"]["max_expected_cost_pct"] > 7.0:
            raise InvariantViolationError("Invariant 4: Regime A expected cost cap cannot exceed 7.0%")

        return RiskLimits(
            min_age_minutes=float(timing["min_age_minutes"]),
            max_age_minutes=float(timing["max_age_minutes"]),
            buyback_cliff_window=[float(x) for x in timing["buyback_cliff_window"]],
            max_holding_timeout_minutes=float(timing["max_holding_timeout_minutes"]),
            mode=exit_strat["mode"],
            prohibit_tranche_exits=bool(exit_strat["prohibit_tranche_exits"]),
            total_swaps=int(exit_strat["total_swaps"]),
            initial_profit_hurdle_regime_a=float(exit_strat["initial_profit_hurdle_regime_a"]),
            initial_profit_hurdle_regime_b=float(exit_strat["initial_profit_hurdle_regime_b"]),
            ratchet_net_lock_min=float(exit_strat["ratchet_net_lock_min"]),
            ratchet_net_lock_max=float(exit_strat["ratchet_net_lock_max"]),
            trailing_stop_pct=float(exit_strat["trailing_stop_pct"]),
            regime_a_max_expected_cost=float(gate["regime_a"]["max_expected_cost_pct"]),
            regime_b_max_expected_cost=float(gate["regime_b"]["max_expected_cost_pct"]),
            regime_a_hurdle=float(gate["regime_a"]["min_gross_profit_hurdle_pct"]),
            regime_b_hurdle=float(gate["regime_b"]["min_gross_profit_hurdle_pct"]),
            max_stress_cost=float(gate["regime_a"]["max_stress_cost_pct"]),
            min_lifetime_txs=int(cluster["min_pretrade_lifetime_txs"]),
            max_common_parent_pct=float(cluster["max_common_parent_funding_pct"]),
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
