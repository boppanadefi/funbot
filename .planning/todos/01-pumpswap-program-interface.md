---
id: todo-01-pumpswap-program-interface
title: PumpSwap program interface (IDL, instructions)
area: execution
status: pending
created: 2026-10-18
priority: high
tags: [pumpswap, idl, solana, instructions, anchor]
---

# PumpSwap Program Interface (IDL, Instructions)

## Objective
Implement binary instruction builders, account metas, and parser interfaces for the canonical PumpSwap program ID (`6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P`).

## Context & Requirements
- Target canonical PumpSwap program ID: `6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P`.
- Extract and map Anchor IDL instruction discriminators:
  - `buy`: 8-byte discriminator + amount + max_sol_cost + accounts.
  - `sell`: 8-byte discriminator + amount + min_sol_output + accounts.
  - Pool / bonding curve account layout deserialization (reserves, virtual reserves, complete flag).
- Program Derived Addresses (PDAs): bonding curve PDA, associated bonding curve token account, global config PDA.
- Clean Python data structures for constructing instructions without heavy node.js dependencies.

## Verification
- Unit test instruction encoding against known real on-chain transaction logs.
- Deserialization test with real PumpSwap bonding curve account data.
