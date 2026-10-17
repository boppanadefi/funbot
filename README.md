# Funbot — Systematic Solana Post-Graduation Momentum Engine

Funbot is a rules-driven, quantitative research and trading system designed for **canonical SOL-quoted PumpSwap pools** on Solana. It targets secondary momentum **8 to 25 minutes** post-graduation, avoiding the extreme adverse selection of sub-second bonding-curve sniping and protocol buyback cliffs.

---

## Documentation Index

* **[Strategy & Mathematical Specification (bot.md)](file:///c:/Users/sivab/Documents/products/funbot/bot.md)** (v2.5)  
  *Detailed mathematical models, PumpSwap canonical tiered fee schedules (1.25% to 0.30%), Path A+ dynamic trailing single-exit, Vega-Lite required-hit-rate surface, and quantitative validation standards.*
* **[Implementation Roadmap & Phase 1 Checklist (build.md)](file:///c:/Users/sivab/Documents/products/funbot/build.md)** (v2.5)  
  *14-day telemetry checklist, executable falsification gate script (`Phase1FalsificationGate`), phased deliverables, repository structure, and risk register.*
* **[Phase Dependency Graph & Data App Specification (ROADMAP.md)](file:///c:/Users/sivab/Documents/products/funbot/ROADMAP.md)** (v2.5)  
  *Full inter-phase coupling analysis (file overlap, semantic APIs, data flow), Streamlit telemetry terminal architecture, and parallelization matrix.*

---

## The 7 Invariant System Mitigations

Every module and agent in this repository strictly adheres to these seven design invariants:

1. **Sub-Minute Sniping Abandoned:** No bonding-curve sniping. The strategy pivots strictly to secondary momentum on canonical PumpSwap pools observed **8 to 25 minutes** post-graduation.
2. **Path A+ (100% Dynamic Trailing Single Exit):** Tranche / scaled exits (Path B) are strictly prohibited for positions < $50. Rather than capping winners at a fixed target, Path A+ holds 100% of the position and trails the peak price. Once the initial hurdle (+16% to +22% in Regime A) is crossed, the stop on 100% of tokens ratchets to lock in +8% to +10% net profit, then trails 10%–15% below peak. When momentum breaks, 100% is liquidated in a single transaction—capturing multi-hundred-percent runs on the entire position while strictly incurring only 2 transactions total.
3. **Protocol Buyback Cliff Avoidance:** Enforces an **8-minute stabilization cooldown** post-graduation, completely bypassing the 0–5 and 5–8 minute BOOST buyback cliff where volume evaporates.
4. **Pre-Flight Double Gate:** Strict pre-flight friction caps (7.0% Regime A, 6.0% Regime B) and gross profit hurdles (+16% to +22% Regime A, +12% to +16% Regime B) guaranteeing a 10%–14% net profit buffer.
5. **Cluster & Burner Risk Filtering (Phase 2):** Excludes buyers with <3 lifetime pre-trade transactions; traces wallet funding 2 hops back and blocks candidate tokens if >30% originate from a common parent address.
6. **Tip Caps & Transaction Structuring:** 1.5% notional entry tip cap; places the Jito tip instruction *after* the swap instruction in the atomic bundle; allocates a dedicated emergency exit budget of up to 0.005 SOL (~$0.80).
7. **Pre-Trade Risk Capacity & Hard Checks:** Verifies `Stressed Loss + Exit Reserve` fits into the remaining $10 daily risk budget before opening; audits mint/freeze authorities; enforces a 20-minute fallback holding timeout.

---

## Current Project Phase: Phase 1 (Observational Telemetry)

Phase 1 is a **14-day zero-key, zero-execution observational run** designed to falsify or validate the candidate supply and friction feasibility before any engineering spend on Phases 2–5.

### Phase 1 Falsification Gate
At the end of the 14-day run, `phase1_falsification_gate.py` evaluates:
* **Candidate Supply:** $\ge 3$ candidates/day surviving the 8-minute buyback cliff (Kill if $<1$/day).
* **Executable Depth:** $\ge 20\%$ of candidates have depth for $20 with $<2\%$ price impact.
* **Friction Feasibility:** Modeled friction $\le 7.0\%$ on $\ge 20\%$ of candidates (Kill if friction $>$ upside on 100%).

If kill criteria are triggered, the project is terminated immediately without further engineering spend. If pass criteria are met, development advances to Phase 2.
