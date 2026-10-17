"""
Unit tests for Phase 1 Falsification Gate logic.
Tests both PASS scenarios and KILL triggers.
"""

from phase1_falsification_gate import Phase1FalsificationGate


def test_falsification_gate_kill_on_insufficient_supply():
    # Only 5 candidates over 14 days -> 0.36 cands/day (< 1.0 kill threshold)
    mock_data = {
        "observation_days": 14,
        "pools": [
            {
                "quote_asset": "SOL",
                "is_canonical": True,
                "minutes_post_graduation": 15.0,
                "price_impact_20usd_pct": 1.1,
                "modeled_friction_pct": 5.5,
            }
            for _ in range(5)
        ],
    }

    gate = Phase1FalsificationGate(mock_data)
    decision, report = gate.run_evaluations()

    assert decision == "KILL"
    assert "Candidate supply too thin" in report["kill_reason"]


def test_falsification_gate_pass_scenario():
    # 56 candidates over 14 days -> 4.0 cands/day (>= 3.0 target)
    mock_data = {
        "observation_days": 14,
        "pools": [
            {
                "quote_asset": "SOL",
                "is_canonical": True,
                "minutes_post_graduation": 12.0,
                "vol_0_to_5m": 60.0,
                "vol_5_to_8m": 10.0,
                "buyback_cliff_decay_pct": 83.3,
                "price_impact_20usd_pct": 1.2,
                "modeled_friction_pct": 5.8,
            }
            for _ in range(56)
        ],
    }

    gate = Phase1FalsificationGate(mock_data)
    decision, report = gate.run_evaluations()

    assert decision == "PASS"
    assert report["candidates_per_day"] == 4.0
    assert report["depth_pass_rate_pct"] == 100.0
    assert report["friction_pass_rate_pct"] == 100.0
