---
id: todo-25-authority-change-realtime-monitor
title: Authority Change Real-Time WebSocket Monitor
area: telemetry
status: pending
created: 2026-10-18
priority: high
tags: [telemetry, websocket, authority, rug-pull, emergency-exit]
---

# Authority Change Real-Time WebSocket Monitor

## Objective
Establish real-time WebSocket event streaming monitoring on-chain instruction logs for all actively held tokens to detect rug vectors (`SetAuthority`, `CloseAccount`, `FreezeAccount`) and fire immediate emergency exits.

## Context & Requirements
- Target: `src/telemetry/authority_monitor.py`.
- Key Logic:
  1. WebSocket `logsSubscribe` filtered for active token mint accounts and pool addresses.
  2. Detect critical rug signatures:
     - `SetAuthority`: Mint authority or freeze authority reassignment.
     - `CloseAccount`: Premature closure of pool or token reserve accounts.
     - `FreezeAccount`: Freeze instructions targeting the bot's associated token account.
  3. Action Trigger:
     - Immediately invoke `emergency_exit_cache.fire_emergency_exit(position_id)`.
     - Dispatch high-priority critical alert via `AlertDispatcher`.
- Orchestrator Integration:
  - Spawn an authority monitor task for each open position in `MasterTradingOrchestrator`.

## Verification
- Unit tests with mock WebSocket log events verifying instant emergency exit dispatch on `SetAuthority` or `FreezeAccount` detection.
