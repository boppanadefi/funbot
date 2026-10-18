---
id: todo-30-solana-threat-model-evm-bias-report
title: Solana Threat Model vs EVM Bias Documentation
area: docs
status: pending
created: 2026-10-18
priority: low
tags: [docs, threat-model, solana, evm, architecture, report]
---

# Solana Threat Model vs EVM Bias Documentation

## Objective
Author a comprehensive architectural report documenting the fundamental differences between Solana's BPF runtime and EVM architectures, dispelling EVM-centric assumptions and articulating Funbot's Solana verification stack.

## Context & Requirements
- Target: `docs/architecture/SOLANA_THREAT_MODEL.md` and comparison report.
- Topics to Document:
  1. **BPF Bytecode vs EVM Bytecode:** Solana programs are compiled eBPF ELF binaries with strict memory isolation and static account access lists, contrasted with EVM dynamic storage slots.
  2. **Program Immutability by Default:** Unlike EVM upgradeable proxies, Solana programs are immutable unless explicitly deployed under `BPFLoaderUpgradeable2`.
  3. **Token-2022 Attack Vectors:** Specific threats unique to Solana: transfer fees, confidential transfers, non-transferable extensions, and hook programs.
  4. **Compute Units vs Gas Pricing:** Discrete 1.4M CU limits per transaction, local fee markets, and priority fee dynamics.
  5. **Jito vs Flashbots Bundles:** Atomic bundle inclusion, tip floor mechanics, canonical tip accounts, and out-of-protocol tip instructions.
  6. **Associated Token Account (ATA) Model vs `approve()`:** No infinite ERC-20 allowances exist on Solana; rent-exemption and account closure take precedence.
  7. **400ms Slot Time Implications:** Why reactive mempool frontrunning is unviable on Solana and proactive structural gates are mandatory.
  8. **Funbot Solana Verification Stack:** Mapping each of the 16 entry checks to Solana-specific mechanics.

## Verification
- Review completed markdown document for technical accuracy and clear architectural diagrams.
