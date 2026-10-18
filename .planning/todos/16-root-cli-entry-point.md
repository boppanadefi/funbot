---
id: todo-16-root-cli-entry-point
title: Unified root CLI entry point (main.py / __main__.py)
area: core
status: pending
created: 2026-10-18
priority: high
tags: [cli, entrypoint, main, runner, daemon]
---

# Unified Root CLI Entry Point (`main.py` / `__main__.py`)

## Objective
Implement a single, clear root entry point allowing operators to launch, inspect, dry-run, and monitor Funbot from the command line.

## Context & Requirements
- Create `main.py` in repository root with `argparse` / `click` subcommands:
  - `python main.py daemon`: Starts `MasterTradingOrchestrator` async event loop.
  - `python main.py terminal`: Launches Streamlit dashboard UI on `localhost:8501`.
  - `python main.py dry-run`: Runs zero-capital dry-run simulation pipeline against live Helius RPC and Jito endpoints.
  - `python main.py status`: Prints active wallet mode, Section 7.1 bankroll isolation state, circuit breaker status, and RPC health.
  - `python main.py baseline`: Runs network latency profiler across Helius and 5 Jito global regions.
- Ensure proper signal handling (`SIGINT`, `SIGTERM`) for graceful teardown.

## Verification
- Test all subcommands (`--help`, `status`, `dry-run`) exit cleanly with code 0.
