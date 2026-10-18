---
id: todo-03-main-event-loop-orchestrator
title: Main event loop / orchestrator
area: core
status: completed
created: 2026-10-18
completed: 2026-10-18
priority: critical
tags: [orchestrator, event-loop, asyncio, pipeline]
---

# Main Event Loop / Orchestrator

## Objective
Establish the primary asynchronous event-driven orchestrator coordinating the Helius WebSocket feed, Virtual AMM state mirror, strategy evaluation, position management, and execution bridges.

## Context & Requirements
- Event Ingestion: Consume real-time `LiveSwapEvent` and `LiveAccountChangeEvent` from the `EventBus`.
- Pool Lifecycle Tracking: Maintain active 8–25 minute post-graduation candidates.
- Concurrency Management: Enforce single-position active constraint (or 3-position override in `REGIME_TEST`).
- Backpressure & Circuit Breakers: Halt or throttle loop if WebSocket latency exceeds 1,000ms staleness ceiling.
- Graceful Shutdown: Handle SIGINT / SIGTERM signals cleanly, persisting all open states and closing sockets.

## Implementation Details
- Delivered `MasterTradingOrchestrator` in `src/orchestrator/main_loop.py`.
- Coordinates `AsyncScheduler`, `StatePersistenceManager`, `AlertDispatcher`, `EventBus`, `DataStalenessCircuitBreaker`, `DailyCircuitBreaker`, and `EmergencyKillSwitch`.
- Verified via `tests/unit/test_phase9_orchestrator.py` (all tests passing).
