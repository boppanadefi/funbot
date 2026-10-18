---
id: todo-23-cpi-dependency-audit-whitelist
title: CPI Dependency Audit & Program Whitelisting (Check 16)
area: security
status: pending
created: 2026-10-18
priority: high
tags: [security, cpi, bpf, bytecode, whitelist, entry-gate]
---

# CPI Dependency Audit & Program Whitelisting (Check 16)

## Objective
Inspect and audit cross-program invocation (CPI) dependencies in custom or wrapper contracts to ensure interactions remain restricted to verified system programs.

## Context & Requirements
- Target: `src/security/authority_auditor.py` and `src/engine/entry_orchestrator.py` (Check 16).
- Key Logic:
  1. `check_cpi_dependencies(program_id, rpc_client, whitelist)`:
     - Fetch program bytecode data.
     - Scan instruction references for cross-program invocations (`sol_invoke` / `sol_invoke_signed`).
     - Extract all external program public keys invoked by the program.
     - Validate every referenced program ID against canonical whitelist:
       - SPL Token Program: `TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA`
       - Token-2022 Program: `TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb`
       - Compute Budget Program: `ComputeBudget111111111111111111111111111111`
       - Associated Token Account: `ATokenGPvbdGVxrPbRdR3Q6z5sQ6fH7sQ2d7d7d7d7`
       - PumpSwap Program: Canonical PumpSwap ID
       - System Program: `11111111111111111111111111111111`
  2. If any referenced program is unrecognized or unverified, reject candidate immediately.
- Orchestrator Integration:
  - Add as Check 16 in `EntryDecisionOrchestrator`.

## Verification
- Unit tests with mock program bytecode: whitelisted CPI calls pass, unverified foreign program invocations rejected.
