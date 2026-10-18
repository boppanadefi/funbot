---
id: todo-13-async-architecture-scheduling-framework
title: Async architecture / scheduling framework
area: architecture
status: completed
created: 2026-10-18
completed: 2026-10-18
priority: high
tags: [asyncio, scheduling, architecture, workers, queues]
---

# Async Architecture / Scheduling Framework

## Objective
Design the complete asynchronous concurrency architecture, task scheduling framework, and worker pools for non-blocking slot-level trade execution.

## Context & Requirements
- Event Queues & Concurrency:
  - High-priority execution queue for time-critical swaps, TP1 exits, and emergency dumps.
  - Medium-priority queue for candidate filtering and 12-check validation.
  - Low-priority queue for telemetry persistence and historical database writes.
- Worker Pools:
  - WebSocket ingestion listener worker (dedicated task).
  - Position monitoring & timer tick worker (runs every 100ms).
  - Background RPC sync & balance refresh worker (runs every 15s).
- Thread Safety & Shared State:
  - Protect active position and ledger state with `asyncio.Lock`.
  - Prevent race conditions during rapid multi-token graduation bursts.

## Implementation Details
- Delivered `AsyncScheduler`, `TaskPriority` (HIGH=1, MEDIUM=2, LOW=3), and `PrioritizedTask` in `src/orchestrator/scheduler.py`.
- Thread-safe state mutation protected via `asyncio.Lock`.
- Worker pool management with backpressure and task draining.
- Verified via `tests/unit/test_phase9_orchestrator.py` (all tests passing).
