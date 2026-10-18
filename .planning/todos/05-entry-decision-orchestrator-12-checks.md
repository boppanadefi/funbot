---
id: todo-05-entry-decision-orchestrator-12-checks
title: Entry decision orchestrator (12 checks)
area: strategy
status: pending
created: 2026-10-18
priority: critical
tags: [entry, filters, 12-checks, risk-gates, order-flow]
---

# Entry Decision Orchestrator (12 Checks)

## Objective
Implement the comprehensive 12-check entry gate orchestrator that must evaluate to 100% PASS before any candidate pool can trigger a buy signal.

## Context & The 12 Checks
1. **Age Window**: Between 8.0 and 25.0 minutes post-graduation (Guardrail 1 & 3).
2. **Canonical Pair**: Canonical PumpSwap pair (`quote_mint == WSOL`, canonical program ID).
3. **Velocity of Unique Wallets (VUW)**: $\ge 1.20$ (current 1m unique buyers / prior 1m unique buyers).
4. **Aggression Ratio**: Taker Buy SOL / Taker Sell SOL $> 2.00$.
5. **Baseline Demand Floor**: 60-second net taker buy volume $\ge \$100/\text{min}$ ($\ge \$50/\text{min}$ in test).
6. **Freeze Authority**: Permanently revoked / burned on-chain.
7. **Mint Authority**: Permanently revoked / burned on-chain.
8. **Top-10 Concentration**: Non-bonding-curve top 10 holders possess $\le 25.0\%$ of circulating supply.
9. **Dynamic Liquidity Sizing Floor**: Calculated max safe swap ($\le 1.5\%$ price impact) $\ge \$3.00$.
10. **Pre-Trade Risk Capacity**: Stressed loss + exit reserve $\le$ remaining daily risk budget (Ceiling: -$10.00).
11. **Emergency Exit Reserve**: Wallet balance after trade maintains $\ge 0.005$ SOL.
12. **WS Data Freshness**: Last WebSocket message received $\le 1000\text{ ms}$ ago.

## Verification
- Unit test with combinatorial matrix: verify entry triggers if and only if all 12 checks pass; verify rejection reasons for every individual failed check.
