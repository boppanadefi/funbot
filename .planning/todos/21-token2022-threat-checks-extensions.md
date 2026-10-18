---
id: todo-21-token2022-threat-checks-extensions
title: Token-2022 Threat Checks & Extension Audit (Check 14)
area: security
status: pending
created: 2026-10-18
priority: critical
tags: [token2022, transfer-fee, security, extensions, entry-gate]
---

# Token-2022 Threat Checks & Extension Audit (Check 14)

## Objective
Implement Solana-specific Token-2022 program extension audits to detect malicious or restrictive extensions (transfer fees, confidential transfers, non-transferability) prior to trade entry.

## Context & Requirements
- Target: `src/security/authority_auditor.py` and `src/engine/entry_orchestrator.py` (Check 14).
- Key Sub-Checks:
  1. `check_token2022_transfer_fee(mint_pubkey, rpc_client)`:
     - Fetch mint account data via `getAccountInfo`.
     - Detect if mint owner is Token-2022 (`TokenzQdBNbLqP5VEhdk33z3BAsQGq3ztC2FGEtCC` / `TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb`).
     - Deserialize `TransferFeeConfig` extension from TLV account data.
     - Extract `transfer_fee_basis_points` and `maximum_fee`.
     - Reject if transfer fee exceeds 2.0% (configurable threshold). Standard Token program or fee $\le 2\%$ passes.
  2. `check_token2022_confidential_transfer(mint_pubkey, rpc_client)`:
     - Detect if mint enables `ConfidentialTransferMint` extension.
     - Hard-reject if active (cannot accurately compute AMM order flow or friction on obfuscated balances).
  3. `check_token2022_non_transferable(mint_pubkey, rpc_client)`:
     - Detect if mint enables `NonTransferable` extension.
     - Hard-reject if active (tokens cannot be sold once bought).
- Orchestrator Integration:
  - Add all three checks under Check 14 in `EntryDecisionOrchestrator`.

## Verification
- Unit tests validating mock Token-2022 mint accounts: standard token pass, fee $\le 2\%$ pass, fee $> 2\%$ reject, confidential transfer reject, non-transferable reject.
