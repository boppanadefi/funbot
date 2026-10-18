---
id: todo-28-compute-budget-exhaustion-defense
title: Compute Budget Exhaustion Defense & Dynamic CU Optimization
area: execution
status: pending
created: 2026-10-18
priority: medium
tags: [compute-budget, simulation, execution, solana, optimization]
---

# Compute Budget Exhaustion Defense & Dynamic CU Optimization

## Objective
Implement dynamic compute unit (CU) estimation and budget defense via binary search simulation, rejecting candidates whose transfer hooks or programs require excessive compute resources while setting optimal CU limits for real executions.

## Context & Requirements
- Target: `src/live/wire_tx_builder.py` and `src/engine/execution_simulator.py`.
- Key Logic:
  1. `check_compute_budget(pool, wallet, amount_sol, rpc_client)`:
     - Assemble transaction with default CU limit (200,000).
     - Simulate via `simulateTransaction`.
     - If simulation raises `ComputationalBudgetExceeded`:
       - Probe iteratively at 400,000, 800,000, and 1,400,000 (Solana block transaction limit).
       - If required compute units $> 1,400,000$, hard-reject candidate (token transfer logic too computationally intensive to execute on-chain).
     - If within bounds, set transaction CU limit = `simulated_cu * 1.20` (20% safety buffer).
  2. Repeat check on simulated sell transaction to ensure exits can fit comfortably within budget.
- Pipeline Integration:
  - Wire into `ExecutionBridge` and `WireTransactionBuilder`.

## Verification
- Unit tests verifying CU estimation, 20% buffer application, and rejection of $>1.4$M CU transactions.
