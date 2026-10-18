---
id: todo-08-amm-math-sell-quote-simulation
title: AMM math / sell quote simulation
area: amm
status: pending
created: 2026-10-18
priority: high
tags: [amm, math, constant-product, quotes, slippage, reserves]
---

# AMM Math / Sell Quote Simulation

## Objective
Implement high-fidelity constant-product $x \cdot y = k$ and PumpSwap reserve math to calculate exact price quotes, self-impact, and minimum outputs for single and time-sliced exits.

## Context & Requirements
- Constant Product Formula:
  $$\Delta \text{SOL} = \frac{\text{sol\_reserve} \cdot \Delta \text{tokens}}{\text{token\_reserve} + \Delta \text{tokens}} \cdot (1 - \text{fee})$$
- Virtual vs Real Reserves: Accurately account for Pump.fun initial virtual SOL reserve (typically 30 SOL) and token reserve scaling ($10^6$ decimals).
- Multi-Slice Sell Quotes:
  - Simulate the exact state of reserves after Slice 1 (30%) to quote Slice 2 (30%), and after Slice 2 to quote Slice 3 (30%).
  - Apply adversarial MEV front-runner sandwich penalties (+0.5%, +1.0%, +1.5%).
- Min Output Slippage Protection: Derive strict `min_sol_output` parameter for the on-chain sell instruction.

## Verification
- Cross-check computed quotes against real PumpSwap on-chain swap events to verify $\le 0.1\%$ divergence.
