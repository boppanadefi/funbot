# Phase 10: Mainnet Micro-Capital Deployment & Dry-Run — Scope

**Phase:** 10-mainnet-micro-capital-deployment  
**Milestone:** Milestone 3 — Autonomous Mainnet Live Execution & Micro-Capital Trading (v4.0)  
**Execution Mode:** Simulation Mode (Zero Live Capital / No Real Private Key Required)  
**Target Delivery:** High-Fidelity Dry-Run Pipeline, Dual-Path Simulation Engine, Network Latency Profiler, and Automated Test Suite  

---

## Objectives

1. **Simulated Zero-Capital Sub-Wallet Architecture:**
   - Operate in deterministic simulation mode without requiring live private keys or depositing real funds.
   - Generate valid RFC 8032 Ed25519 signatures and wire transactions for Mainnet simulation.
   - Enforce Section 7.1 Bankroll Isolation rules ($25.00 active limit, $75.00 cold reserve, 0.005 SOL exit reserve) within the simulation context.

2. **Dual-Path Zero-Capital Dry-Run Engine:**
   - Assemble full wire transactions (ComputeBudget, ATA, PumpSwap swap, Jito tip transfer) using real-time Mainnet blockhashes from Helius RPC.
   - **Path 1 (Helius `simulateTransaction`):** Validate on-chain transaction viability, account metadata, and compute unit usage without broadcasting.
   - **Path 2 (Jito Bundle Validation):** Verify bundle payload ordering, tip transfer instruction placement (strictly post-swap), canonical tip account addressing, and the 1.5% notional tip cap.

3. **Network Latency & Endpoint Baselining:**
   - Benchmark round-trip latencies for Helius HTTP RPC, Helius WSS, and all 5 Jito Block Engine endpoints (Frankfurt, Amsterdam, NY, Tokyo, Default).
   - Dynamically identify and select the fastest Jito region for bundle routing.

4. **Operator CLI & Automated Test Suite:**
   - Provide `scripts/run_dry_run_pipeline.py` for one-command dry-run verification.
   - Provide unit tests in `tests/unit/test_phase10_deployment.py` maintaining 100% test pass rate.
