# Phase 6: Protocol & AMM Engine

**Milestone:** Milestone 2 — Live Execution Engine & Production Orchestration (v3.0)  
**Status:** ACTIVE SPRINT (Planning & Implementation)  
**Dependencies:** None  
**Related Todos:**
- [todo-01: PumpSwap Program Interface (IDL, Instructions)](file:///c:/Users/sivab/Documents/products/funbot/.planning/todos/01-pumpswap-program-interface.md)
- [todo-08: AMM Math / Sell Quote Simulation](file:///c:/Users/sivab/Documents/products/funbot/.planning/todos/08-amm-math-sell-quote-simulation.md)
- [todo-07: On-Chain Authority Auditor](file:///c:/Users/sivab/Documents/products/funbot/.planning/todos/07-onchain-authority-auditor.md)

---

## Deliverables & Scope

1. **PumpSwap Program Interface (`src/protocol/pumpswap.py`):**
   - Anchor 8-byte instruction discriminators for `buy` and `sell`.
   - Program Derived Addresses (PDAs): Bonding Curve PDA, Associated Bonding Curve Token Account, Global Config PDA.
   - Exact instruction layout serialization matching Anchor binary wire format.
   - Bonding curve account data deserializer (virtual token reserves, virtual sol reserves, real token reserves, real sol reserves, complete flag).

2. **AMM Math & Sell Quote Simulation (`src/amm/quote_calculator.py`):**
   - Precise constant-product swap quotes ($k = x \cdot y$).
   - Exact token output for buy input and exact SOL output for sell input.
   - Price impact and slippage calculations with basis-point accuracy.
   - Stressed sell quote factoring in 2.0% slippage allowance and round-trip fees.

3. **On-Chain Authority Auditor (`src/security/authority_auditor.py`):**
   - Validation that `mintAuthorityOption == 0` (Mint authority permanently disabled).
   - Validation that `freezeAuthorityOption == 0` (Freeze authority permanently disabled).
   - Validation that metadata pointer and mutability flags prevent backdoored modifications.
   - Real RPC integration test using `getAccountInfo` with Base64/Base58 parsing.

4. **Unit & Integration Test Suite (`tests/unit/test_phase6_protocol_engine.py`):**
   - Test instruction serialization against verified on-chain PumpSwap transactions.
   - Test AMM math against simulated and real bonding curves.
   - Test authority auditor against legitimate and honeypot tokens.
