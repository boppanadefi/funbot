# Funbot Systematic Architecture Overview

This directory contains architectural specifications, state machines, and protocol mappings for the Funbot post-graduation momentum trading system on Solana.

## Core Reference Documents
- **[bot.md](file:///c:/Users/sivab/Documents/products/funbot/bot.md):** Master mathematical and quantitative strategy specification (The 7 Invariants, empirical falsifications, and cost models).
- **[PROJECT.md](file:///c:/Users/sivab/Documents/products/funbot/.planning/PROJECT.md):** Project goals, milestone status, and tech stack.
- **[ROADMAP.md](file:///c:/Users/sivab/Documents/products/funbot/.planning/ROADMAP.md):** Systematic roadmap across Milestones 1, 2, and 3.

## System Subsystems
1. **Protocol & AMM Engine (`src/protocol/`, `src/amm/`):** Binary Anchor IDL encoders, constant-product AMM integer math, and SPL mint authority auditor.
2. **Strategy & State Engine (`src/engine/`):** 12-Check Entry Decision Gate, Path A+ Position State Machine, and 5 concurrent async timers.
3. **Execution & MEV Pipeline (`src/live/`):** Pure-Python Solana wire transaction builder, multi-region Jito bundle client (Invariant 6 tip caps), and confirmation monitor.
4. **Orchestrator & Production Reliability (`src/orchestrator/`):** 3-tier priority async scheduler, SQLite WAL persistence, Discord/Telegram alerting, and master event loop.
5. **Observational Telemetry (`src/telemetry/`):** Real-time Helius RPC/WSS feed, taker flow metrics, and SQLite WAL telemetry storage.
6. **Streamlit Operational Terminal (`src/ui/`):** Dark-mode Zinc operator cockpit with real-time paper ledger and Gemini AI assistant.
