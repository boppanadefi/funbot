---
id: todo-22-program-upgradeability-check-bpf
title: Program Upgradeability Check (Check 15)
area: security
status: pending
created: 2026-10-18
priority: high
tags: [security, bpf, upgradeability, immutability, entry-gate]
---

# Program Upgradeability Check (Check 15)

## Objective
Implement Solana's counterpart to EVM proxy contract detection by verifying program account immutability and loader state before trading tokens interacting with custom programs.

## Context & Requirements
- Target: `src/security/authority_auditor.py` and `src/engine/entry_orchestrator.py` (Check 15).
- Key Logic:
  1. `check_program_not_upgradeable(program_id, rpc_client)`:
     - Query program account info via `getAccountInfo`.
     - Check if account owner is `BPFLoaderUpgradeable2` (`BPFLoaderUpgradeab1e11111111111111111111111`).
     - If upgradeable: inspect program data account `upgrade_authority_address`. If authority exists and slot $> 0$, flag as upgradeable risk (code can be arbitrarily modified post-deployment).
     - If owner is `BPFLoader` / `BPFLoader2` (immutable) or upgrade authority is `None`/finalized, pass.
- Orchestrator Integration:
  - Add as Check 15 in `EntryDecisionOrchestrator`.

## Verification
- Unit tests verifying upgradeable program account rejection and immutable program account approval.
