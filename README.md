# Funbot — Systematic Solana Post-Graduation Momentum Engine

Funbot is a rules-driven, quantitative research and trading system designed for **canonical SOL-quoted PumpSwap pools** on Solana. It targets secondary momentum **3 to 25 minutes** post-graduation, avoiding the extreme adverse selection of sub-second bonding-curve sniping and protocol buyback cliffs.

---

## Documentation Index

* **[Strategy & Mathematical Specification (bot.md)](file:///c:/Users/sivab/Documents/products/funbot/bot.md)** (v2.5)  
  *Detailed mathematical models, PumpSwap canonical tiered fee schedules (1.25% to 0.30%), Friction-Busting 80/20 scale-out, local-structure discrete exits, dynamic liquidity-based sizing, real-time order flow dynamics, Vega-Lite required-hit-rate surface, and quantitative validation standards.*
* **[Implementation Roadmap & Phase 1 Checklist (build.md)](file:///c:/Users/sivab/Documents/products/funbot/build.md)** (v2.5)  
  *14-day telemetry checklist, executable falsification gate script (`Phase1FalsificationGate`), phased deliverables, repository structure, and risk register.*
* **[Phase Dependency Graph & Data App Specification (ROADMAP.md)](file:///c:/Users/sivab/Documents/products/funbot/ROADMAP.md)** (v2.5)  
  *Full inter-phase coupling analysis (file overlap, semantic APIs, data flow), Streamlit telemetry terminal architecture, and parallelization matrix.*

---

## The 7 Invariant System Mitigations

Every module and agent in this repository strictly adheres to these invariant rules:

1. **Sub-Minute Sniping Abandoned:** No bonding-curve sniping. The strategy pivots strictly to secondary momentum on canonical PumpSwap pools observed **3 to 25 minutes** post-graduation.
2. **Invariant 2: Path A+ is Dead (< $50) — Replaced by Friction-Busting 80/20 Scale-Out & Structural Exits:** Path A+ (100% dynamic trailing) creates an unworkable 0.14:1 R:R ratio on positions < $50. Replaced by Friction-Busting 80/20 Scale-Out: TP1 sells 80% at +8%–+10% gross (+9% default) to cover all round-trip friction; remaining 20% runs as a Free Runner riding discrete candle-low structure stops. Discrete structural exits: (1) **Structure-Based Trailing Stop:** Stop Loss = Lowest Low of the previous two completed 1-minute candles. (2) **Momentum Time-Decay Exit:** Exit 100% if 2 consecutive 1m candles fail to make a new high and volume drops >40%. (3) **Liquidity Collapse Exit:** 30s sell-quote simulation exits immediately if slippage > 5.0%.
3. **Invariant 3: 8-Minute Cooldown is Dead — Replaced by Volume Exhaustion Consolidation + Breakout:** Static 8m cooldown is replaced with dynamic market structure validation: (1) 3-minute minimum cooldown post-graduation (bypasses 0-3m protocol cliff); (2) Consolidation Trigger: 1m volume drops to < 20% of graduation spike peak 1m volume; (3) Breakout Entry: candle closes above initial spike resistance with buy volume > 1.5x consolidation average.
4. **Invariant 4: Pre-Flight Double Gate Friction Caps Remain — Ratchet Triggers Replaced by TP1 Friction Buster:** Friction caps remain strictly enforced (Expected $\le 7.0\%$ in Regime A, $\le 6.0\%$ in Regime B, Stressed $\le 10.0\%$), but legacy Ratchet Triggers (+16% to +22%) are dead, replaced by the **+8% to +10% TP1 Friction Buster** (+9% default target) unlocking the free runner.
5. **Invariant 5: Cluster/Burner History Checks are Dead — Replaced by Real-Time Order Flow Dynamics:** 2-hop funding graphs and <3 transaction heuristics are dead (bypassed by aged wallets and mixers). Replaced by Real-Time AMM Order Flow Dynamics: (1) **Velocity of Unique Wallets (VUW):** `(Unique Buyers Current 1m) / (Unique Buyers Prior 1m)` $\ge 1.20$ on breakout; (2) **Aggression Ratio:** `Taker Buy SOL / Taker Sell SOL` $> 2.00$ into the AMM curve.
6. **Capital Allocation: Fixed $10–$25 Sizing is Dead — Replaced by Dynamic Liquidity-Based Sizing Capped at $25:** Position size is a dependent variable of pool liquidity: $\text{Position Size} = \min(\text{Calculated Max Safe Swap at 1.5% impact}, \$25.00)$. Hard reject floor if Max Safe Swap $< \$3.00$. Tip caps (1.5% notional entry tip cap, atomic bundle placement after swap, 0.005 SOL emergency exit budget).
7. **Pre-Trade Risk Capacity & Hard Checks:** Verifies `Stressed Loss + Exit Reserve` fits into the remaining $10 daily risk budget before opening; audits mint/freeze authorities; enforces a 20-minute fallback holding timeout.

---

## Current Project Phase: Phase 1 (Observational Telemetry)

Phase 1 is a **14-day zero-key, zero-execution observational run** designed to falsify or validate the candidate supply and friction feasibility before any engineering spend on Phases 2–5.

### Phase 1 Falsification Gate
At the end of the 14-day run, `phase1_falsification_gate.py` evaluates:
* **Candidate Supply:** $\ge 3$ candidates/day surviving the 8-minute buyback cliff (Kill if $<1$/day).
* **Executable Depth:** $\ge 20\%$ of candidates have depth for $20 with $<2\%$ impact OR support dynamic liquidity sizing ($\ge \$3.00$ at $\le 1.5\%$ impact).
* **Friction Feasibility:** Modeled friction $\le 7.0\%$ on $\ge 20\%$ of candidates (Kill if friction $>$ upside on 100%).

If kill criteria are triggered, the project is terminated immediately without further engineering spend. If pass criteria are met, development advances to Phase 2.
