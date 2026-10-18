---
id: todo-02-transaction-construction-submission
title: Transaction construction & submission
area: execution
status: pending
created: 2026-10-18
priority: high
tags: [transaction, serialization, submission, compute-budget, solana]
---

# Transaction Construction & Submission

## Objective
Build a robust Solana versioned transaction builder and submission pipeline that packages compute budget units, dynamic priority fees, ATA creation, and program instructions into signed wire format.

## Context & Requirements
- Dynamic Compute Budget: Set compute unit limit (`SetComputeUnitLimit`) and priority fee unit price (`SetComputeUnitPrice`).
- Idempotent ATA Creation: Include `createAssociatedTokenAccountIdempotent` instruction only when needed for buy legs.
- Recent Blockhash Lifecycle: Manage 150-slot valid blockhash cache with background renewal.
- Wire Serialization: Serialize transaction message, compact-u16 signature headers, and payload into Base58 / Base64 binary wire format.
- Submission Dispatch: Dual-route submission via Helius Dedicated RPC (`sendTransaction`) and Jito Block Engine.

## Verification
- Validate serialized transaction byte length and signature count against Solana protocol spec.
- Test simulation via `simulateTransaction` on Helius RPC.
