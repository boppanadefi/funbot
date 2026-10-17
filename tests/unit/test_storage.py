"""
Unit tests for TelemetryStorage SQLite database persistence.
"""

from datetime import datetime, timezone
from pathlib import Path
import pytest
from src.telemetry.storage import TelemetryStorage


@pytest.fixture
def temp_storage(tmp_path):
    db_file = tmp_path / "test_telemetry.db"
    return TelemetryStorage(db_path=str(db_file))


def test_upsert_pool_and_query(temp_storage):
    pool_data = {
        "pool_address": "test_pool_1",
        "token_mint": "mint_1",
        "quote_mint": "So11111111111111111111111111111111111111112",
        "is_canonical": True,
        "graduation_slot": 100,
        "graduation_ts": datetime.now(timezone.utc).isoformat(),
        "current_sol_reserve": 85.0,
        "market_cap_sol": 410.0,
        "state": "ACTIVE_WINDOW",
        "price_impact_20usd_pct": 1.25,
        "modeled_friction_pct": 5.4,
    }
    temp_storage.upsert_pool(pool_data)

    pools = temp_storage.get_all_pools()
    assert len(pools) == 1
    assert pools[0]["pool_address"] == "test_pool_1"
    assert pools[0]["state"] == "ACTIVE_WINDOW"

    active = temp_storage.get_active_candidates()
    assert len(active) == 1
    assert active[0]["token_mint"] == "mint_1"


def test_cliff_metrics_and_gate_export(temp_storage):
    pool_data = {
        "pool_address": "test_pool_2",
        "token_mint": "mint_2",
        "quote_mint": "So11111111111111111111111111111111111111112",
        "is_canonical": True,
        "graduation_slot": 200,
        "graduation_ts": datetime.now(timezone.utc).isoformat(),
        "current_sol_reserve": 110.0,
        "market_cap_sol": 550.0,
        "state": "ACTIVE_WINDOW",
        "price_impact_20usd_pct": 1.1,
        "modeled_friction_pct": 5.2,
    }
    temp_storage.upsert_pool(pool_data)

    cliff_data = {
        "pool_address": "test_pool_2",
        "vol_0_to_5m": 80.0,
        "vol_5_to_8m": 12.0,
        "vol_8_to_25m": 25.0,
        "cliff_decay_pct": 85.0,
    }
    temp_storage.upsert_cliff_metrics(cliff_data)

    export = temp_storage.export_gate_dataset(observation_days=14)
    assert export["total_pools"] == 1
    pool_rec = export["pools"][0]
    assert pool_rec["vol_0_to_5m"] == 80.0
    assert pool_rec["buyback_cliff_decay_pct"] == 85.0


def test_cluster_evaluation_persistence(temp_storage):
    pool_data = {
        "pool_address": "test_pool_cluster",
        "token_mint": "mint_cluster",
        "quote_mint": "So11111111111111111111111111111111111111112",
        "is_canonical": True,
        "graduation_slot": 300,
        "graduation_ts": datetime.now(timezone.utc).isoformat(),
        "current_sol_reserve": 90.0,
        "market_cap_sol": 420.0,
        "state": "ACTIVE_WINDOW",
    }
    temp_storage.upsert_pool(pool_data)

    eval_data = {
        "pool_address": "test_pool_cluster",
        "token_mint": "mint_cluster",
        "total_buyers": 15,
        "burner_count": 2,
        "burner_volume_ratio": 0.12,
        "top_parent_pubkey": "RootFunder1",
        "top_parent_concentration": 0.20,
        "sybil_detected": False,
        "mint_revoked": True,
        "freeze_revoked": True,
        "composite_risk_score": 0.15,
        "is_approved": True,
        "rejection_reasons": [],
    }
    temp_storage.upsert_cluster_evaluation(eval_data)

    retrieved = temp_storage.get_cluster_evaluation("test_pool_cluster")
    assert retrieved is not None
    assert retrieved["pool_address"] == "test_pool_cluster"
    assert retrieved["total_buyers"] == 15
    assert retrieved["composite_risk_score"] == 0.15
    assert retrieved["is_approved"] == 1
    assert retrieved["rejection_reasons"] == []

