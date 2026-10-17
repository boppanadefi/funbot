# Pump.fun Post-Graduation Momentum System — Build Plan & Phase 1 Checklist (v2.5)

**Status:** Implementation Roadmap — Post-Falsification Architecture  
**Supersedes:** v2.4, v2.3, v2.2, v2.1, v2.0, and v1.0  
**Scope:** Canonical SOL-quoted PumpSwap pools only (non-canonical and USDC pools excluded).  
**Build Philosophy:** Measure first, engineer second. Phase 1 must arm itself with falsification criteria before any spend on Phases 2/3. Complete dependency map documented in [ROADMAP.md](file:///c:/Users/sivab/Documents/products/funbot/ROADMAP.md).

---

## 1. Project Timeline Overview

| Phase | Duration | Deliverable | Gate Criteria | Go/No-Go Decision |
| :--- | :--- | :--- | :--- | :--- |
| **Phase 1** | 14 days | Telemetry engine (observational) | Candidate supply, friction feasibility | Terminate or proceed |
| **Phase 1.5** | Concur. (Day 3+) | Streamlit Telemetry Terminal + Gemini AI Chat | Real-time falsification gate & NL telemetry queries | Visual confirmation |
| **Phase 2** | 21 days | Cluster analyzer (risk scoring) | Phase 1 pass + 20% candidate pool | Proceed to Phase 3 |
| **Phase 3** | 14 days | Cost engine (accounting) | Phase 2 pass + Phase 1 metrics | Proceed to Phase 4 |
| **Phase 4** | 28 days | Paper trading (simulation) | Positive out-of-sample expectancy | Proceed to live |
| **Phase 5** | Ongoing | Live micro-trading | Operator oversight, 10–20 hand-reviewed trades | Scale or terminate |

### 1.1 The 7 Invariant System Mitigations (Build Guardrails)

Every service, module, and agent in this repository must strictly adhere to these seven invariant rules:

1. **Sub-Minute Sniping Abandoned:** No bonding curve sniping. Strict focus on secondary momentum on canonical PumpSwap pools **8 to 25 minutes** post-graduation.
2. **Path A+ (100% Dynamic Trailing Single Exit):** Tranche / scaled exits (Path B) are strictly prohibited for positions < $50. Apply 100% of position notional to capture uncapped upside with a dynamic trailing stop. When the initial profit hurdle is crossed, ratchet the stop on 100% of the position to lock in +8% to +10% net profit, then trail peak executable price by 10%–15%. When momentum breaks, exit 100% in a single transaction, eliminating tranche dilution and restricting execution to strictly 2 swaps total.
3. **Protocol Buyback Cliff Avoidance:** Enforce an **8-minute stabilization cooldown** post-graduation, completely bypassing the 0–5 and 5–8 minute BOOST buyback cliff.
4. **Pre-Flight Double Gate:** Strict pre-flight friction caps (7.0% Regime A, 6.0% Regime B) and gross profit hurdles (+16% to +22% Regime A, +12% to +16% Regime B) guaranteeing a 10%–14% net profit buffer.
5. **Cluster & Burner Risk Filtering (Phase 2):** Exclude buyers with <3 lifetime pre-trade transactions; trace funding 2 hops back and block candidates if >30% originate from a common parent address.
6. **Tip Caps & Transaction Structuring:** 1.5% notional entry tip cap; place Jito tip instruction *after* swap instruction in the atomic bundle; allocate up to 0.005 SOL dedicated emergency exit budget.
7. **Pre-Trade Risk Capacity & Hard Checks:** Verify `Stressed Loss + Exit Reserve` fits into remaining $10 daily risk budget before opening; audit mint/freeze authorities; enforce 20-minute fallback holding timeout.

---

## 2. Phase 1: Observational Telemetry Engine (14 Days)

### 2.1 Phase 1 Responsibilities (No Trading Code)
1. **Canonical Pool Discovery:** Monitor the PumpSwap program for pool creation and migration events. Verify canonical status and identify quote asset (SOL vs USDC).
2. **State Tracking:** Track each pool through the 8–25 minute stabilization window. Record token price, reserve levels, and market-cap tier transitions.
3. **Basic Flow Telemetry:** Ingest raw swap events; compute rolling 10s, 30s, 60s volume, buy/sell transaction counts, and raw unique wallet counts.
4. **Feed Reliability & Telemetry Audit:** Log feed completeness, stream disconnects, reconnection latency, and reserve reconciliation deltas.
5. **Observational Watchlist States:** Maintain pool states: `MIGRATED`, `STABILIZING`, `TRACKING`, `DROPPED`.

### 2.2 Phase 1 Falsification Gate Script (Pseudo-Code)

```python
#!/usr/bin/env python3
"""
Phase 1 Falsification Gate — 14-Day Telemetry Analysis
Run this script after 14 days of observational data collection.
Returns PASS or KILL based on empirical candidate supply and friction feasibility.
"""

import json
from datetime import datetime, timedelta
from typing import List, Dict, Tuple

class Phase1FalsificationGate:
    
    # Configuration constants
    MIN_CANDIDATES_PER_DAY = 3
    MIN_DENSITY_THRESHOLD = 1  # candidates/day to continue
    MIN_DEPTH_PERCENTILE = 0.20  # 20% of candidates must pass depth screen
    MAX_ACCEPTABLE_FRICTION = 7.0  # % of notional
    BUYBACK_CLIFF_MINUTES = (5, 8)  # Skip entries in this window
    
    def __init__(self, telemetry_data_path: str):
        """Load 14-day telemetry dataset."""
        self.telemetry_data = self._load_telemetry(telemetry_data_path)
        self.candidates = self._extract_candidates()
        
    def _load_telemetry(self, path: str) -> Dict:
        """Load JSON telemetry from Phase 1 run."""
        with open(path, 'r') as f:
            return json.load(f)
    
    def _extract_candidates(self) -> List[Dict]:
        """
        Extract candidate tokens that:
        1. Graduated to PumpSwap (canonical pool detected)
        2. Survived past 8-minute mark (post-buyback cliff)
        3. Have measurable volume in 8–25 min window
        """
        candidates = []
        for pool in self.telemetry_data['pools']:
            if pool['status'] == 'MIGRATED' and pool['quote_asset'] == 'SOL':
                age_at_assessment = pool.get('minutes_post_graduation', 0)
                if age_at_assessment >= self.BUYBACK_CLIFF_MINUTES[1]:
                    # Apply depth filter
                    depth_ok = self._check_depth_screen(pool)
                    if depth_ok:
                        candidates.append({
                            'token_mint': pool['token_mint'],
                            'graduation_ts': pool['migration_ts'],
                            'volume_1m': pool['vol_1m_avg'],
                            'volume_3m': pool['vol_3m_avg'],
                            'impact_estimate': pool.get('price_impact_est', 0),
                            'holder_concentration': pool.get('top10_concentration', 100),
                            'friction_model': self._model_friction(pool)
                        })
        return candidates
    
    def _check_depth_screen(self, pool: Dict) -> bool:
        """Check if pool can absorb $20 with <2% price impact."""
        # Simplified constant-product model
        reserve_sol = pool.get('reserve_quote_sop', 0)
        reserve_token = pool.get('reserve_base_token', 0)
        
        if reserve_sol <= 0 or reserve_token <= 0:
            return False
            
        # Approximate: impact ≈ swap_size / (2 * reserve)
        swap_size_sol = 20.0 / 155.0  # Convert $20 to SOL at $155/SOL
        estimated_impact = swap_size_sol / (2 * reserve_sol) * 100
        
        return estimated_impact < 2.0
    
    def _model_friction(self, pool: Dict) -> float:
        """
        Calculate expected friction for Path A (single exit) on this candidate.
        Returns friction as percentage of notional.
        """
        # Venue fees (tier-dependent)
        market_cap_sol = pool.get('market_cap_sol', 0)
        if market_cap_sol < 420:
            venue_fee = 1.250  # Regime A
        else:
            venue_fee = max(1.200, min(0.300, 1.200 - (market_cap_sol - 420) / 200))  # Regime B decay
        
        # Round-trip venue fees
        venue_total = venue_fee * 2
        
        # Price impact (assumed from depth screen)
        impact = pool.get('price_impact_est', 1.5)
        
        # Transaction overheads (2 legs: entry + exit)
        # Assume $15 position, SOL=$155
        sol_notional = 15.0 / 155.0  # ~0.097 SOL
        tip_per_leg = 0.001  # 0.001 SOL p50 tip
        base_fee = 0.000005
        priority_fee = 0.0002
        overhead_per_leg = base_fee + priority_fee + tip_per_leg
        overhead_total_pct = (overhead_per_leg * 2) / sol_notional * 100
        
        total_friction = venue_total + impact + overhead_total_pct
        return total_friction
    
    def run_gate_checks(self) -> Tuple[str, Dict]:
        """
        Execute all falsification criteria.
        Returns ('PASS', details) or ('KILL', details).
        """
        report = {}
        
        # CHECK 1: Candidate Supply
        days_in_dataset = 14
        avg_candidates_per_day = len(self.candidates) / days_in_dataset
        report['candidates_per_day'] = avg_candidates_per_day
        
        if avg_candidates_per_day < self.MIN_DENSITY_THRESHOLD:
            report['reason'] = 'Insufficient candidate supply'
            return ('KILL', report)
        
        report['candidate_supply_status'] = 'PASS'
        
        # CHECK 2: Depth Screen Pass Rate
        depth_pass_count = sum(1 for c in self.candidates if c['impact_estimate'] < 2.0)
        depth_pass_rate = depth_pass_count / len(self.candidates) if self.candidates else 0
        report['depth_pass_rate'] = depth_pass_rate
        
        if depth_pass_rate < self.MIN_DEPTH_PERCENTILE:
            report['reason'] = 'Most candidates lack executable depth'
            return ('KILL', report)
        
        report['depth_status'] = 'PASS'
        
        # CHECK 3: Friction Feasibility
        friction_values = [c['friction_model'] for c in self.candidates]
        friction_pass_count = sum(1 for f in friction_values if f <= self.MAX_ACCEPTABLE_FRICTION)
        friction_pass_rate = friction_pass_count / len(friction_values) if friction_values else 0
        
        report['avg_friction_pct'] = sum(friction_values) / len(friction_values) if friction_values else 0
        report['friction_pass_rate'] = friction_pass_rate
        
        if friction_pass_rate < self.MIN_DEPTH_PERCENTILE:
            report['reason'] = 'Friction exceeds upside on most candidates'
            return ('KILL', report)
        
        report['friction_status'] = 'PASS'
        
        # CHECK 4: Buyback Cliff Verification
        volume_collapse_detected = False
        for pool in self.telemetry_data['pools']:
            if pool.get('minutes_post_graduation') in self.BUYBACK_CLIFF_MINUTES:
                vol_drop = (pool.get('vol_1m_pre_cliff', 1) - pool.get('vol_1m_post_cliff', 0)) / pool.get('vol_1m_pre_cliff', 1)
                if vol_drop > 0.5:  # >50% volume collapse at cliff
                    volume_collapse_detected = True
                    break
        
        if not volume_collapse_detected:
            # No collapse means 5–8 minute window is safe — we're targeting 8–25 anyway
            report['buyback_cliff_status'] = 'VERIFIED'
        else:
            # Collapse detected — our 8–25 min window avoids it, so OK
            report['buyback_cliff_status'] = 'AVOIDED (8–25 min window)'
        
        # ALL CHECKS PASSED
        report['decision'] = 'PROCEED TO PHASE 2'
        report['next_steps'] = [
            'Build Phase 2 cluster analyzer',
            'Build Phase 3 cost engine',
            'Prepare out-of-sample validation dataset'
        ]
        
        return ('PASS', report)
    
    def generate_report(self, result: Tuple[str, Dict]) -> str:
        """Format results for human consumption."""
        status, details = result
        lines = [
            '=' * 60,
            f"PHASE 1 FALSIFICATION GATE REPORT",
            f"Generated: {datetime.utcnow().isoformat()}Z",
            '-' * 60,
            f"Final Decision: {details.get('decision', status)}",
            '-' * 60,
            ""
        ]
        
        if status == 'KILL':
            lines.extend([
                f"⚠️  KILL CRITERION TRIGGERED",
                f"Reason: {details.get('reason', 'Unknown')}",
                "",
                "Details:",
                f"  • Candidates/day: {details.get('candidates_per_day', 0):.2f}",
                f"  • Depth pass rate: {details.get('depth_pass_rate', 0):.1%}",
                f"  • Friction pass rate: {details.get('friction_pass_rate', 0):.1%}",
                "",
                "Recommended Actions:",
                "  1. Archive all telemetry data",
                "  2. Document lessons learned",
                "  3. Consider alternative venues or strategies",
                "  4. Do NOT proceed to Phase 2 engineering"
            ])
        else:
            lines.extend([
                f"✅ PASS CRITERIA MET",
                "",
                "Metrics:",
                f"  • Candidates/day: {details.get('candidates_per_day', 0):.2f}",
                f"  • Depth pass rate: {details.get('depth_pass_rate', 0):.1%}",
                f"  • Avg friction: {details.get('avg_friction_pct', 0):.2f}%",
                f"  • Friction pass rate: {details.get('friction_pass_rate', 0):.1%}",
                f"  • Buyback cliff: {details.get('buyback_cliff_status', 'N/A')}",
                "",
                "Next Steps:",
            ])
            for step in details.get('next_steps', []):
                lines.append(f"  • {step}")
        
        lines.append('=' * 60)
        return '\n'.join(lines)


if __name__ == '__main__':
    import sys
    
    telemetry_path = sys.argv[1] if len(sys.argv) > 1 else 'phase1_telemetry_14day.json'
    
    try:
        gate = Phase1FalsificationGate(telemetry_path)
        result = gate.run_gate_checks()
        report = gate.generate_report(result)
        print(report)
        
        # Exit code determines CI/CD pipeline outcome
        if result[0] == 'KILL':
            sys.exit(1)  # Fail pipeline
        else:
            sys.exit(0)  # Continue to Phase 2
            
    except Exception as e:
        print(f"ERROR: {e}")
        sys.exit(2)  # Unexpected error
```

### 2.3 Phase 1 Acceptance Benchmarks
Phase 1 is complete when the telemetry engine demonstrates:
* **Detection Lag:** Mean pool discovery latency $< 2.0\text{ seconds}$ from on-chain block timestamp.
* **Stream Resilience:** Automatic reconnection within $< 5.0\text{ seconds}$ of network dropout without process termination.
* **Reserve Accuracy:** Zero drift between locally tracked reserves and periodic on-chain RPC snapshots ($< 0.01\%$ discrepancy).
* **Zero Fictional Data:** Missing data or gaps are recorded as `GAP_DETECTED`, never interpolated as active volume.

---

## 3. Phase 2: Cluster Analyzer & Risk Scoring (21 Days)

### 3.1 Phase 2 Deliverables
1. **Shared Funding Graph Traversal:** Implement 2-hop ancestry tracing for recent buyer wallets.
2. **Cluster Risk Score Computation:** Assign `cluster_risk_score` based on funding concentration.
3. **Fresh Burner Detection:** Flag buyers with $< 3$ lifetime pre-trade transactions.
4. **Sybil Pattern Heuristics:** Detect coordinated launch patterns (same timing, similar amounts).

### 3.2 Phase 2 Acceptance Criteria
* Cluster score correlates with post-trade pump/dump patterns ($r > 0.3$ in backtest).
* Fresh burner exclusion reduces fake volume inflation by measurable margin.
* No false positives on verified CEX hot wallets and liquidity routers.

---

## 4. Phase 3: Cost Engine & Accounting Module (14 Days)

### 4.1 Phase 3 Deliverables
1. **Dynamic Fee-Tier Lookup:** Query current PumpSwap fee tier from on-chain program state.
2. **Slippage Model Calibration:** Calibrate impact estimates using historical trade fills.
3. **Jito Tip Distribution Sampling:** Sample p50 and p75 tips from recent bundle landing conditions.
4. **Failure Penalty Library:** Encode `FailedTxPenalty` values for various rejection modes.

### 4.2 Phase 3 Acceptance Criteria
* Cost predictions within 0.5% of actual incurred costs in paper trading.
* Stress cost calculation matches observed worst-case outcomes in 95% of cases.
* Tip floor enforcement blocks entries when congestion exceeds tip cap.

---

## 5. Phase 4: Paper Trading & Validation (28 Days)

### 5.1 Phase 4 Deliverables
1. **Full Integration:** Combine telemetry + cluster + cost engines into single execution pipeline.
2. **Paper Trading Ledger:** Track all simulated trades with full accounting (gross, fees, net).
3. **Out-of-Sample Test:** Evaluate on chronologically contiguous 40% dataset without parameter adjustment.
4. **Hit-Rate Surface Analysis:** Generate Vega-Lite chart showing $p^*$ distribution across trades.

### 5.2 Phase 4 Acceptance Criteria
* Positive net expectancy across out-of-sample data after all fees.
* Maximum simulated drawdown $< \$10.00$.
* Win rate $p \ge 70\%$ in Regime A or $p \ge 65\%$ in Regime B.
* Business P&L positive after subtracting RPC/VPS costs.

---

## 6. Phase 5: Live Micro-Trading (Ongoing)

### 6.1 Phase 5 Protocols
1. **Manual Oversight:** Operator reviews first 10–20 live trades before automation scales.
2. **Daily Loss Ceiling:** Hard halt at $-\$10.00$ realized loss; requires manual restart.
3. **Position Monitoring:** Real-time dashboard showing open positions, unrealized P&L, daily loss counter.
4. **Emergency Kill-Switch:** Manual override to close all positions immediately.

### 6.2 Phase 5 Success Metrics
* Net P&L positive after 30 live trading days.
* Win rate stabilizes within ±5% of paper trading expectation.
* Max drawdown stays below $-\$10.00$.
* Operating expenses < 20% of net profits.

---

## 7. Repository Structure

```
/pump-fun-bot
├── /src
│   ├── /telemetry          # Phase 1 observational engine
│   ├── /ui                 # Phase 1.5+ Streamlit telemetry & trading terminal
│   ├── /cluster            # Phase 2 risk analyzer
│   ├── /cost               # Phase 3 accounting module
│   ├── /engine             # Phase 4+ execution pipeline
│   └── /utils              # Shared utilities (logging, config)
├── /data
│   ├── /telemetry          # Raw observational logs
│   ├── /backtest           # Historical datasets
│   └── /live               # Live trading ledger
├── /tests
│   ├── /unit               # Unit tests for each module
│   ├── /integration        # Integration tests
│   └── /fixtures           # Test data samples
├── /docs
│   ├── bot.md              # Strategy specification
│   ├── build.md            # This file
│   └── /architecture       # Detailed architecture docs
├── ROADMAP.md              # Phase dependency matrix & data app specs
├── phase1_falsification_gate.py  # Gate-check script (v2.5)
├── requirements.txt
└── README.md
```

---

## 8. Risk Register

| Risk | Probability | Impact | Mitigation |
| :--- | :--- | :--- | :--- |
| **Candidate supply too thin** | High | Critical | Phase 1 falsification gate kills project early |
| **Friction > upside** | High | Critical | Path A enforced; friction budget capped at 7% |
| **Freeze authority exploit** | Medium | Catastrophic | Mint/freeze authority checks in Phase 2 |
| **Protocol buyback cliff misread** | Medium | High | Stabilization window shifted to 8–25 min |
| **Network congestion spike** | Medium | High | Tip cap enforcement; entry skipped if exceeded |
| **Simulation overfitting** | Medium | High | Strict out-of-sample validation; no look-ahead |
| **Operating expense drain** | High | High | Business P&L tracked monthly; shutdown trigger at negative |

---

## 9. Decision Log Template

All major decisions must be logged with justification:

```yaml
date: YYYY-MM-DD
decision: <short title>
context: <what situation prompted this decision?>
alternatives_considered:
  - <alternative A>
  - <alternative B>
decision_rationale: <why this path was chosen>
consequences:
  - <expected consequence 1>
  - <expected consequence 2>
reversible: <yes/no>
review_date: YYYY-MM-DD  # When to reassess this decision
```

---

## 10. Contact & Escalation

* **Technical Issues:** Open GitHub issue with `bug` label
* **Strategy Questions:** Consult `bot.md` first; then open discussion
* **Financial Loss Thresholds:** Immediate operator notification required
* **System Outage (>15 min):** Fail-safe = halt new entries, liquidate existing positions
