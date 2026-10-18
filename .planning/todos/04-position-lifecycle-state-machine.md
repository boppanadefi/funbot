---
id: todo-04-position-lifecycle-state-machine
title: Position lifecycle state machine
area: engine
status: pending
created: 2026-10-18
priority: high
tags: [position, state-machine, lifecycle, transitions]
---

# Position Lifecycle State Machine

## Objective
Formalize deterministic position state transitions with strict entry, scale-out, trailing stop, and closure guarantees.

## Context & Requirements
- States:
  - `IDLE`: No open exposure.
  - `PENDING_ENTRY`: Signal triggered, entry transaction in-flight.
  - `ACTIVE_FULL`: Position filled at entry price, awaiting TP1 target (+9% gross) or stop trigger.
  - `TP1_SCALE_OUT`: 90% position being sold across 3x 30% time-sliced tranches.
  - `RUNNER_ACTIVE`: 10% lottery runner active with discrete 2-minute candle low stop and 3-minute hard time kill (< +30%).
  - `EXIT_IN_FLIGHT`: Exit transaction submitted and awaiting confirmation.
  - `CLOSED`: Position fully realized, accounting committed to ledger.
- Invariants: Disallow skipping states, enforce maximum holding time of 20 minutes, log all transition timestamps and triggers.

## Verification
- Unit test all valid transition paths and verify that illegal transitions (e.g. `IDLE` -> `RUNNER_ACTIVE`) raise strict exceptions.
