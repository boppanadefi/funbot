# Project Definition — Funbot Systematic Solana Momentum Engine

## Context & Objectives
Funbot is a quantitative, rules-driven research and execution engine for **canonical SOL-quoted PumpSwap pools** on Solana. 

Following the empirical falsification of sub-second bonding-curve sniping in v2.2 and v2.3, the system pivots strictly to **secondary post-graduation momentum** observed between **8 and 25 minutes** post-graduation, governed by strict falsification gates and mathematical cost accounting.

---

## Shipped Milestones

### Milestone 1 (v1.0): Observational Telemetry & Falsification Architecture
- **Verdict:** Formally audited and closed on 2026-10-18 ([MILESTONE-1-AUDIT.md](file:///c:/Users/sivab/Documents/products/funbot/.planning/MILESTONE-1-AUDIT.md)).
- **Unit Test Coverage:** 120 / 120 tests passing in `tests/unit/`.
- **Infrastructure:** Live Helius Mainnet RPC & WebSocket integration, Streamlit Zinc UI, zero-dependency Ed25519 cryptographic signer, paper trading engine with Path A+ exits, and 28-day Phase 5 Go/No-Go gate.

### Milestone 2 (v3.0): Live Execution Engine & Production Orchestration
- **Verdict:** Formally audited and closed on 2026-10-18 ([MILESTONE-2-AUDIT.md](file:///c:/Users/sivab/Documents/products/funbot/.planning/MILESTONE-2-AUDIT.md)).
- **Unit & Integration Test Coverage:** 187 / 187 tests passing in `tests/unit/` (100% pass rate in 7.82s).
- **Deliverables:** PumpSwap Anchor IDL binary parser & instruction builders, constant-product AMM quote engine, SPL authority auditor, 12-Check Entry Gate, Path A+ Position State Machine, 5 concurrent exit timers, Solana wire transaction builder, multi-region Jito bundle client, 3-tier priority async scheduler, SQLite WAL crash persistence, and Discord/Telegram alerting.

---

## Active Milestone: Milestone 3 (v4.0) — Autonomous Mainnet Live Execution
**Goal:** Deploy the audited live execution engine into continuous, autonomous production operation on Solana Mainnet with segregated micro-capital ($25 active bankroll, $75 cold reserve), real-time push alerts, and automated 24/7 supervisor monitoring.
- **Phase 10:** Mainnet Micro-Capital Deployment & Dry-Run (zero-capital live verification, sub-wallet setup, latency baseline)
- **Phase 11:** Real-Time Telemetry & Alert Webhooks (Discord embeds, Telegram interactive bot, live Streamlit monitoring)
- **Phase 12:** Production Supervisor & Daemon Management (system service, watchdog restarts, WAL crash recovery)
- **Phase 13:** Live Execution Walk-Forward & Performance Ledger (first live micro-swaps, slippage benchmarking, 28-day scorecard)

---

## The 7 Invariant System Mitigations
1. **Sub-Minute Sniping Abandoned:** No bonding-curve sniping. Target secondary momentum in the 8–25 minute post-graduation window.
2. **Path A+ (100% Dynamic Trailing Single Exit):** Tranche / scaled exits strictly prohibited for positions < $50. Once the profit hurdle is crossed, ratchet the stop on 100% of the position to lock in +8% to +10% net profit, trailing peak executable price by 10%–15%. Liquidation occurs in a single transaction (strictly 2 swaps total, zero tranche dilution).
3. **Protocol Buyback Cliff Avoidance:** Enforce an 8-minute stabilization cooldown to avoid the 0–5 and 5–8 minute BOOST buyback cliff.
4. **Pre-Flight Double Gate:** Expected friction cap (7.0% Regime A, 6.0% Regime B) and gross profit hurdles (+16% to +22% Regime A, +12% to +16% Regime B).
5. **Cluster & Burner Risk Filtering:** Exclude buyers with <3 lifetime pre-trade transactions; trace 2-hop funding graph (>30% common parent cutoff). Revoke freeze/mint authorities and retail top-10 concentration $\le 25.0\%$.
6. **Tip Caps & Transaction Structuring:** 1.5% notional entry tip cap; post-swap tip instruction in bundle; 0.005 SOL dedicated emergency exit budget.
7. **Pre-Trade Risk Capacity & Hard Checks:** Verify `Stressed Loss + Exit Reserve` fits into remaining $10 daily risk budget before opening; 20-minute fallback holding timeout.

---

## Tech Stack
* **Language:** Python 3.11+ (Python 3.14 compatible)
* **Cryptography:** Zero-dependency pure-Python RFC 8032 Ed25519 signer & Base58 codec
* **Data App / Dashboard:** Streamlit + Plotly (Zinc dark mode, minimal chrome, JetBrains Mono data display)
* **Solana RPC:** Dedicated Helius RPC HTTP & WebSocket client (`wss://mainnet.helius-rpc.com/`)
* **MEV & Bundles:** Jito Block Engine JSON-RPC bundle dispatcher (Frankfurt, Amsterdam, NY, Tokyo failover)
* **Storage:** SQLite (`data/telemetry/orchestrator_state.db` & `telemetry.db`) in WAL mode
* **AI Layer:** Gemini AI Data Chat panel with thought segregation and conversational telemetry queries
