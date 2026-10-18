---
id: todo-27-rent-exempt-balance-verification
title: Rent-Exempt Balance Verification for Pool & Token Accounts
area: security
status: pending
created: 2026-10-18
priority: medium
tags: [solana, rent-exempt, balance, security, entry-gate]
---

# Rent-Exempt Balance Verification for Pool & Token Accounts

## Objective
Verify that pool accounts, reserve vaults, and token accounts maintain lamport balances strictly above the Solana rent-exempt threshold to prevent garbage collection or sudden account purging.

## Context & Requirements
- Target: `src/security/authority_auditor.py` or `src/engine/entry_orchestrator.py`.
- Key Logic:
  1. `check_rent_exempt(account_pubkey, rpc_client)`:
     - Query `getAccountInfo` for account lamports and data byte length.
     - Query `getMinimumBalanceForRentExemption(data_length)`.
     - Reject if `lamports < rent_minimum` (account susceptible to zeroing/garbage collection).
  2. Apply check to:
     - PumpSwap pool state account.
     - Token reserve vault.
     - Bot's associated token account.
- Orchestrator Integration:
  - Add as validation sub-check within the entry evaluation pipeline.

## Verification
- Unit tests verifying that sub-rent accounts fail and fully rent-exempt accounts pass.
