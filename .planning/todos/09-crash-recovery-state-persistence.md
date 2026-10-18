---
id: todo-09-crash-recovery-state-persistence
title: Crash recovery / state persistence
area: infrastructure
status: completed
created: 2026-10-18
completed: 2026-10-18
priority: high
tags: [persistence, sqlite, crash-recovery, state, wal]
---

# Crash Recovery / State Persistence

## Objective
Implement atomic state journaling and restart recovery so the trading bot seamlessly resumes active positions, circuit breaker counters, and in-flight orders after process restarts or power cuts.

## Context & Requirements
- SQLite Write-Ahead Logging (WAL): Enable `PRAGMA journal_mode=WAL` for robust concurrent reads/writes without lock contention.
- Persisted State Surfaces:
  - Active positions table (token mint, entry price, size, SL, TP1 status, tranche counters).
  - Daily Circuit Breaker state (`daily_net_loss_usd`, `trades_count`, daily reset timestamp).
  - Open staged orders and in-flight transaction signatures.
- Startup Reconciliation:
  - On engine startup, scan on-chain token accounts via Helius RPC to reconcile local position records with actual wallet SPL token balances.
  - If discrepancy detected (e.g. position closed while bot was offline), automatically reconcile ledger.

## Implementation Details
- Delivered `StatePersistenceManager` in `src/orchestrator/persistence.py`.
- Schema initialized with `PRAGMA journal_mode=WAL`, `persisted_positions`, `persisted_orders`, `circuit_breaker_journal`.
- Implemented `save_position_state`, `load_active_positions`, `commit_closed_position`, `reconcile_startup`.
- Verified via `tests/unit/test_phase9_orchestrator.py` (all tests passing).
