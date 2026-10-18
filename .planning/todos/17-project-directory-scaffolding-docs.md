---
id: todo-17-project-directory-scaffolding-docs
title: Directory scaffolding, documentation & test structure
area: infrastructure
status: pending
created: 2026-10-18
priority: medium
tags: [directories, scaffolding, docs, integration-tests]
---

# Directory Scaffolding, Documentation & Test Structure

## Objective
Establish standard directory scaffolding for documentation, data partitioning, and integration test fixtures.

## Context & Requirements
- Scaffolding Directories:
  - `docs/`: Core architecture references, workflow diagrams, and link/copy to `bot.md`.
  - `docs/architecture/`: Detailed subsystem breakdowns (Protocol, AMM, Strategy, Execution, Orchestrator).
  - `data/backtest/`: Storage for historical backtesting and tick replays (with `.gitkeep`).
  - `data/live/`: Storage for live trade journals and receipts (with `.gitkeep`).
  - `tests/integration/`: High-level multi-engine integration workflows.
  - `tests/fixtures/`: Canonical on-chain transaction dumps, mock RPC payloads, and bonding curve binary samples.

## Verification
- Verify directories are tracked in git via `.gitkeep` files without polluting git with dynamic telemetry databases.
