---
id: todo-24-prebuilt-emergency-exit-cache
title: Pre-Built Emergency Exit Cache for Zero-Delay Rug Response
area: live
status: pending
created: 2026-10-18
priority: high
tags: [live, emergency-exit, cache, rug-response, zero-delay]
---

# Pre-Built Emergency Exit Cache for Zero-Delay Rug Response

## Objective
Pre-construct and sign complete sell exit wire transactions in memory immediately upon position opening, maintaining freshly refreshed blockhashes so emergency dumps can be broadcast instantly without signing latency.

## Context & Requirements
- Target: `src/live/emergency_exit_cache.py`.
- Key Functions:
  1. `build_emergency_sell_tx(pool, wallet, token_amount, keypair)`: Assembles full sell wire transaction (ComputeBudget + PumpSwap sell + Jito tip) and signs it using active wallet keypair.
  2. `cache_emergency_exit(position_id, signed_tx)`: Keeps signed wire payload in high-speed memory cache indexed by `position_id`.
  3. `fire_emergency_exit(position_id)`: Transmits cached payload directly to Jito Block Engine and RPC endpoint with 0ms construction/signing delay.
  4. `invalidate_cache(position_id)`: Removes cached payload on normal position closure.
  5. `refresh_blockhashes()`: Background loop every 30 seconds re-signs all cached emergency exits using fresh mainnet recent blockhashes.
- Pipeline Integration:
  - Call `build_emergency_sell_tx` upon position fill confirmation.
  - Wire into liquidity collapse / authority change listeners to invoke `fire_emergency_exit`.

## Verification
- Integration test: Open position → cache exit → trigger emergency halt → verify pre-signed transaction dispatched with zero signing overhead.
