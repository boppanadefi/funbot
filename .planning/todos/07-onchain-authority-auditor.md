---
id: todo-07-onchain-authority-auditor
title: On-chain authority auditor
area: security
status: pending
created: 2026-10-18
priority: high
tags: [authority, audit, freeze-authority, mint-authority, rug-prevention]
---

# On-Chain Authority Auditor

## Objective
Automate live RPC inspection of token mint, freeze, and metadata authorities to eliminate rug pulls, honeypots, and malicious token extensions before capital commitment.

## Context & Requirements
- Mint Account Query: Query SPL Token mint account via Helius RPC (`getAccountInfo`).
- Authority Verification:
  - `mint_authority` must be `None` (permanently null/revoked).
  - `freeze_authority` must be `None` (permanently null/revoked).
- Token-2022 Extension Checks:
  - Detect transfer fee extensions, permanent delegate extensions, or transfer hooks.
  - Reject any token implementing non-standard extensions that could tax or block secondary sells.
- Caching: Cache verified audit results per mint address with TTL matching the pool observation window.

## Verification
- Unit test against real on-chain account data for both safe (revoked) tokens and honeypot/freeze-active tokens.
