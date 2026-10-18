---
id: todo-18-property-based-testing-hypothesis
title: Property-based testing for AMM constant-product math
area: testing
status: pending
created: 2026-10-18
priority: medium
tags: [testing, property-based, hypothesis, amm, math]
---

# Property-Based Testing for AMM Constant-Product Math

## Objective
Implement property-based fuzz tests using `hypothesis` to mathematically prove constant-product AMM invariants across extreme reserve values.

## Context & Requirements
- Target: `src/amm/quote_calculator.py` and `src/protocol/pumpswap.py`.
- Invariant Properties to Prove:
  1. **Monotonicity:** Larger input SOL amounts strictly produce larger token amounts (for buy) or vice versa.
  2. **Conservation of $k$:** In zero-fee scenarios, $(x + \Delta x)(y - \Delta y) \ge xy$.
  3. **Zero Float Drift:** Integer rounding never yields negative outputs or over-allocates reserve tokens.
  4. **Fee Upper Bounds:** Gross minus net SOL never deviates from $1.0\%$ protocol fee by more than 1 lamport rounding tolerance.
  5. **Extreme Ratio Resilience:** Fuzz test reserves ranging from $10^6$ to $10^{18}$ without overflow or zero-division exceptions.

## Verification
- Run `pytest tests/unit/test_amm_properties.py` across 1,000+ randomized iterations.
