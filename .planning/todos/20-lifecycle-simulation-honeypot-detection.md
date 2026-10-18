---
id: todo-20-lifecycle-simulation-honeypot-detection
title: Lifecycle Simulation & Honeypot Detection (Check 13)
area: engine
status: pending
created: 2026-10-18
priority: critical
tags: [simulation, honeypot, entry-gate, checks, execution]
---

# Lifecycle Simulation & Honeypot Detection (Check 13)

## Objective
Implement end-to-end lifecycle simulation (buy followed by sell simulation) before executing entries to detect honeypots, hidden sell taxes, and compute unit exhaustion before capital is committed.

## Context & Requirements
- Target: `src/engine/execution_simulator.py` and `src/engine/entry_orchestrator.py` (Check 13).
- Components:
  1. `simulate_buy(pool, wallet, amount_sol)`: Transmit buy instruction via JSON-RPC `simulateTransaction`, return tokens received or REJECT.
  2. `simulate_sell(pool, wallet, token_amount)`: Transmit sell instruction with received tokens, return SOL received or REJECT.
  3. `simulate_lifecycle(pool, wallet, amount_sol)`: Run buy sim then sell sim; reject candidate if either simulation errors.
  4. `detect_honeypot(buy_result, sell_result)`: If buy succeeds but sell fails with transfer/freeze error, classify as honeypot and hard-reject.
  5. `detect_hidden_fee(simulated_output, amm_expected_output)`: If simulated output diverges from AMM constant-product expectation by > 2.0%, flag as hidden sell tax and hard-reject.
  6. `estimate_compute_units(tx)`: Binary search simulation with increasing CU limits (200k, 400k, 800k, 1.4M) + 20% buffer.
- Orchestrator Integration:
  - Add `simulate_lifecycle` as Check 13 in `EntryDecisionOrchestrator` (`src/engine/entry_orchestrator.py`).

## Verification
- Unit tests with mock `simulateTransaction` responses: passing cycle, honeypot fail, hidden fee (>2%) fail, and CU exhaustion.
