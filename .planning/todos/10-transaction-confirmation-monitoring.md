---
id: todo-10-transaction-confirmation-monitoring
title: Transaction confirmation monitoring
area: execution
status: pending
created: 2026-10-18
priority: high
tags: [confirmation, signatures, rpc, websocket, slot-latency]
---

# Transaction Confirmation Monitoring

## Objective
Implement real-time on-chain confirmation tracking via WebSocket `signatureSubscribe` and HTTP polling to guarantee deterministic settlement and compute unit analysis.

## Context & Requirements
- Dual Subscription Pipeline:
  - Primary: Helius WebSocket `signatureSubscribe` for slot-level landing notifications.
  - Secondary fallback: HTTP RPC `getSignatureStatuses` polling every 1.5 seconds if WS message is delayed.
- Confirmation Outcomes:
  - `CONFIRMED`: Transaction landed on-chain without error; extract slot, fee, and compute units consumed.
  - `FAILED_ON_CHAIN`: Transaction reverted on-chain (e.g. slippage exceeded); handle error and record venue fee loss.
  - `EXPIRED`: Transaction blockhash expired without inclusion; trigger resubmission or emergency exit protocol.
- Latency Profiling: Calculate elapsed time from submission to landing to populate empirical latency distribution metrics.

## Verification
- Unit test mock confirmation streams for success, revert, and timeout states.
