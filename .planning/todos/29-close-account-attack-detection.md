---
id: todo-29-close-account-attack-detection
title: Close Account Attack Detection for AMM Pools
area: security
status: pending
created: 2026-10-18
priority: medium
tags: [security, close-account, pool-authority, entry-gate]
---

# Close Account Attack Detection for AMM Pools

## Objective
Detect pool structures vulnerable to malicious `CloseAccount` attacks, where an external authority or privileged signer retains the ability to close the liquidity pool or drain lamport reserves.

## Context & Requirements
- Target: `src/security/authority_auditor.py`.
- Key Logic:
  1. `check_close_account_vulnerability(pool_pubkey, rpc_client)`:
     - Verify whether pool state account has an external `authority` that can invoke `close_account`.
     - Confirm that pool `token_vault` account owner strictly matches the canonical program PDA and cannot be transferred or closed by an arbitrary signer.
     - Validate that pool authority is strictly a program-derived address (PDA) with no private key signer.
     - Check account lamport cushion above rent-exemption to guarantee the pool cannot be zeroed out.
- Orchestrator Integration:
  - Add as security sub-check under entry evaluation.

## Verification
- Unit tests verifying that PDA-governed pools pass and externally closable pool structures are rejected.
