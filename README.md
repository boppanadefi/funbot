# Funbot — Systematic Solana Post-Graduation Momentum Engine

Funbot is a rules-driven, quantitative research and trading system designed for **canonical SOL-quoted PumpSwap pools** on Solana. It targets secondary momentum **3 to 25 minutes** post-graduation, avoiding the extreme adverse selection of sub-second bonding-curve sniping and protocol buyback cliffs.

---

## Documentation Index

* **[Strategy & Mathematical Specification (bot.md)](file:///c:/Users/sivab/Documents/products/funbot/bot.md)** (v2.5)  
  *Detailed mathematical models, PumpSwap canonical tiered fee schedules (1.25% to 0.30%), Friction-Busting 90/10 scale-out, local-structure discrete exits, dynamic liquidity-based sizing, real-time order flow dynamics, Vega-Lite required-hit-rate surface, and quantitative validation standards.*
* **[Implementation Roadmap & Phase 1 Checklist (build.md)](file:///c:/Users/sivab/Documents/products/funbot/build.md)** (v2.5)  
  *14-day telemetry checklist, executable falsification gate script (`Phase1FalsificationGate`), phased deliverables, repository structure, and risk register.*
* **[Phase Dependency Graph & Data App Specification (ROADMAP.md)](file:///c:/Users/sivab/Documents/products/funbot/ROADMAP.md)** (v2.5)  
  *Full inter-phase coupling analysis (file overlap, semantic APIs, data flow), Streamlit telemetry terminal architecture, and parallelization matrix.*

---

## The 7 Invariant System Mitigations

Every module and agent in this repository strictly adheres to these invariant rules:

1. **Sub-Minute Sniping Abandoned:** No bonding-curve sniping. The strategy pivots strictly to secondary momentum on canonical PumpSwap pools observed **3 to 25 minutes** post-graduation.
2. **Invariant 2: Path A+ is Dead (< $50) — Replaced by Friction-Busting 90/10 Scale-Out & Structural Exits:** Path A+ (100% dynamic trailing) creates an unworkable 0.14:1 R:R ratio on positions < $50. Furthermore, holding a 20% runner indefinitely is bagholder logic (if price reverses, it bleeds all profits away). Replaced by Friction-Busting 90/10 Scale-Out: (1) **TP1 ("The Friction Buster"):** Sells 90% at +8%–+10% gross (+9% default) via **Time-Sliced TP1 (3 equal 30% sells spaced 2-3 blocks / ~800ms–1.2s apart to defeat MEV sandwich attacks and eliminate AMM curve cascades)**, locking in +$1.62 gross on a $20 position to completely cover the ~$1.50 round-trip friction; (2) **The Runner (10% Lottery Runner):** Remaining 10% ($2) runs as a psychological lottery ticket. If it dumps to 0, gross loss is only -$0.38 (vs -$2.56 on 80/20 and -$3.50 on 100% stop); (3) **Hard Time Kill on Runner:** If the 10% runner does not achieve $\ge +30\%$ gross within 3 minutes of TP1, market dump it immediately to ruthlessly cut exposure; (4) **Discrete Structure-Based Trailing Stop:** For runners achieving $\ge +30\%$, Stop Loss = Lowest Low of the previous two completed 1-minute candles; (5) **Momentum Time-Decay Exit:** Exit 100% if 2 consecutive 1m candles fail to make a new high and volume drops >40%; (6) **Liquidity Collapse Exit:** 30s sell-quote simulation exits immediately if slippage > 5.0%.
3. **Invariant 3: 8-Minute Cooldown is Dead — Volume Exhaustion Consolidation + Baseline Demand Floor + Breakout:** Static 8m cooldown is replaced with dynamic market structure validation: (1) 3-minute minimum cooldown post-graduation (bypasses 0-3m protocol cliff); (2) Consolidation Trigger: 1m volume drops to < 20% of graduation spike peak 1m volume; (3) **Baseline Demand Floor (Dead Token Protection):** Volume drop is necessary but not sufficient. Absolute 1-minute volume must remain $\ge \$100\text{ USD/min}$ during consolidation. If volume collapses to $\$10/\text{min}$, retail has abandoned the token; hard reject as a dead token to avoid buying isolated whale sweeps on illiquid books. If volume drops to $\$150/\text{min}$ but is $< 20\%$ of peak, that is healthy consolidation; (4) Breakout Entry: candle closes above initial spike resistance with buy volume > 1.5x consolidation average.
4. **Invariant 4: Pre-Flight Double Gate Friction Caps Remain — Ratchet Triggers Replaced by TP1 Friction Buster:** Friction caps remain strictly enforced (Expected $\le 7.0\%$ in Regime A, $\le 6.0\%$ in Regime B, Stressed $\le 10.0\%$), but legacy Ratchet Triggers (+16% to +22%) are dead, replaced by the **+8% to +10% TP1 Friction Buster** (+9% default target) unlocking the 10% lottery runner (with 3-minute hard time kill).
5. **Invariant 5: Cluster/Burner History Checks are Dead — Replaced by Slot-Level (400ms) Order Flow Dynamics:** 2-hop funding graphs and <3 transaction heuristics are dead (bypassed by aged wallets and mixers). Furthermore, 1-minute rolling windows (VUW and Aggression) are lagging by 150 blocks and trivially spoofable by syndicates (firing 20 fresh wallets in block N, triggering bot entry in block N+1, and dumping in block N+2). Replaced by Slot-Level (400ms) Order Flow: (1) **Slot-Level Delta Unique Buyers (last 5 blocks):** Requires $\Delta\text{Unique Buyers}_{\text{last 5 blocks}} \ge 1$ (400ms slots $\approx 2.0\text{s}$) to demand continuous organic buyer inflow and defeat block N syndicate batch spoofs; (2) **Breakout Candle Taker Buy Dominance (> 70%):** Requires that $> 70\%$ of the SOL moved in the breakout candle was Taker Buy (`Taker Buy SOL / Total SOL Volume > 0.70`), confirming curve momentum is overwhelmingly aggressive market-buy led and defeating balanced wash trades.
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
