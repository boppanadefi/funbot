---
id: todo-06-exit-timer-system-5-concurrent-timers
title: Exit timer system (5 concurrent timers/pos)
area: engine
status: pending
created: 2026-10-18
priority: high
tags: [exit, timers, runner, tp1, risk-limits]
---

# Exit Timer System (5 Concurrent Timers / Position)

## Objective
Implement asynchronous, non-blocking multi-timer management for each active position to enforce precise exit rules and timeouts.

## Context & The 5 Timers
1. **TP1 Time-Slice Pacing Timer**: Slices the 90% TP1 exit into 3 separate 30% sells spaced over deterministic block delays to absorb MEV front-running impact.
2. **3-Minute Runner Hard Time Kill**: If the 10% lottery runner fails to reach $\ge +30\%$ gross within 180 seconds of TP1 completion, forcefully market-dump the runner.
3. **20-Minute Fallback Holding Timeout**: Maximum lifespan of any trade. Forces unconditional liquidation at minute 20 to prevent capital stagnation.
4. **Trailing Candle Stop Update Tick**: 30-second polling timer that recalculates the discrete lowest-low of the previous 2 closed 2-minute candles.
5. **Transaction Confirmation Timeout**: 30-second watchdog timer for in-flight transactions. If unconfirmed after 30 seconds, triggers cancel/resubmit protocol.

## Verification
- Test timer execution using `asyncio` simulated time leaps and ensure no timer leaks or unhandled task exceptions occur.
