# Project Definition — Funbot Systematic Solana Momentum Engine

## Context & Objectives
Funbot is a quantitative, rules-driven research and execution engine for **canonical SOL-quoted PumpSwap pools** on Solana. 

Following the empirical falsification of sub-second bonding-curve sniping in v2.2 and v2.3, the system pivots strictly to **secondary post-graduation momentum** observed between **8 and 25 minutes** post-graduation, governed by strict falsification gates and mathematical cost accounting.

## The 7 Invariant System Mitigations
1. **Sub-Minute Sniping Abandoned:** No bonding-curve sniping. Target secondary momentum in the 8–25 minute post-graduation window.
2. **Path A+ (100% Dynamic Trailing Single Exit):** Tranche / scaled exits strictly prohibited for positions < $50. Once the profit hurdle is crossed, ratchet the stop on 100% of the position to lock in +8% to +10% net profit, trailing peak executable price by 10%–15%. Liquidation occurs in a single transaction (strictly 2 swaps total, zero tranche dilution).
3. **Protocol Buyback Cliff Avoidance:** Enforce an 8-minute stabilization cooldown to avoid the 0–5 and 5–8 minute BOOST buyback cliff.
4. **Pre-Flight Double Gate:** Expected friction cap (7.0% Regime A, 6.0% Regime B) and gross profit hurdles (+16% to +22% Regime A, +12% to +16% Regime B).
5. **Cluster & Burner Risk Filtering (Phase 2):** Exclude buyers with <3 lifetime pre-trade transactions; trace 2-hop funding graph (>30% common parent cutoff).
6. **Tip Caps & Transaction Structuring:** 1.5% notional entry tip cap; post-swap tip instruction in bundle; 0.005 SOL dedicated emergency exit budget.
7. **Pre-Trade Risk Capacity & Hard Checks:** Verify `Stressed Loss + Exit Reserve` fits into remaining $10 daily risk budget before opening; 20-minute fallback holding timeout.

## Tech Stack
* **Language:** Python 3.11+
* **Data App / Dashboard:** Streamlit + Plotly (Zinc dark mode, minimal chrome, JetBrains Mono data display)
* **Solana RPC:** WebSocket / Geyser / RPC HTTP client
* **Storage:** SQLite (`data/telemetry/telemetry.db`) & Parquet (`data/telemetry/pools_observed.parquet`)
* **AI Layer:** Gemini AI Data Chat panel with thought segregation and conversational telemetry queries
