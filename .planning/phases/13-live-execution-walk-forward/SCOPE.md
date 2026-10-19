# Phase 13: Live Execution Walk-Forward & Performance Ledger — Scope

**Phase:** 13-live-execution-walk-forward  
**Milestone:** Milestone 3 — Autonomous Mainnet Live Execution & Micro-Capital Trading (v4.0)  
**Execution Mode:** Production Walk-Forward Validation & Performance Attribution  
**Target Delivery:** Live Trade Ledger + Slippage Attribution Engine + Walk-Forward Comparative Validator + 28-Day Performance Scorecard + Streamlit Tab 10 UI + Comprehensive Test Suite  

---

## Objectives

1. **Live Trade Ledger & SQLite WAL Journal (`src/live/live_ledger.py`):**
   - High-fidelity recording of all live on-chain micro-trades in `orchestrator_state.db` (`live_trades` table).
   - Captures comprehensive execution telemetry:
     - `position_id`, `pool_address`, `token_mint`, `regime`, `entry_signature`, `exit_signature`
     - `entry_timestamp`, `exit_timestamp`, `duration_minutes`
     - `quoted_entry_price_sol`, `actual_entry_fill_price_sol`, `entry_slippage_pct`
     - `quoted_exit_price_sol`, `actual_exit_fill_price_sol`, `exit_slippage_pct`, `total_slippage_pct`
     - `sol_spent`, `sol_received`, `actual_venue_fee_sol`, `actual_priority_fee_sol`, `actual_jito_tip_sol`
     - `net_pnl_sol`, `net_pnl_usd`, `net_return_pct`, `exit_reason`, `ratchet_activated`
     - `mev_sandwich_detected` (flagging if intermediate front/back-run occurred in transaction block)
   - Supports atomic SQLite WAL commits with zero data loss across reboots.

2. **Real-World Slippage & MEV Friction Attribution Engine (`src/live/friction_attribution.py`):**
   - Compares live executed fill prices against theoretical constant-product AMM quotes (`ConstantProductAmm` / integer math).
   - Decomposes trade performance into discrete friction drivers:
     - **Gross Theoretical Alpha:** Edge prior to network friction.
     - **Venue Fee Drag:** PumpSwap protocol and LP fee cost (0.25% or canonical curve fee).
     - **Execution Slippage Impact:** Difference between expected quote and confirmed on-chain execution.
     - **MEV & Priority Friction:** Jito bundle tips (capped at 1.5% notional) + Solana compute budget priority fees.
     - **Sandwich Attack Resistance:** Verifies whether trade suffered an adverse sandwich by inspecting slot reserve states.

3. **Live vs Paper Walk-Forward Validator (`src/live/walk_forward_validator.py`):**
   - Conducts continuous side-by-side walk-forward benchmarking comparing Paper Ledger executions against Live Micro executions.
   - Evaluates key divergence metrics:
     - **Slippage Variance:** $\le 2.0\%$ acceptable variance between paper and live execution.
     - **Win-Rate Divergence:** Measures whether live fills maintain the paper trading win rate ($\ge 60\%$ on TP1).
     - **Latency Degradation:** Assesses fill-delay impact between signal observation and block inclusion.

4. **28-Day Autonomous Performance Scorecard (`src/live/live_scorecard.py`):**
   - Evaluates the rolling 28-day live performance against Milestone 3 quantitative criteria:
     1. **Net Expectancy (SOL & USD):** Must be $> 0.0$ after all on-chain fees, slippage, and tips.
     2. **Win Rate:** $\ge 60.0\%$ on dynamic trailing / profit targets.
     3. **Maximum Drawdown:** $< \$10.00$ (strictly respecting the Section 7.1 daily circuit breaker).
     4. **Slippage Variance:** $< 2.0\%$ average one-way deviation from model quotes.
     5. **Profit Factor & Return on Active Bankroll:** Tracks net return on $25 active capital.

5. **CLI & Streamlit Live Terminal Integration:**
   - CLI subcommands in `main.py`:
     - `python main.py walkforward`: Prints the walk-forward scorecard and friction attribution summary.
     - `python main.py trade --micro`: Initiates a controlled $3.00 micro-swap live test adhering to the 12-Check Gate and Path A+ exit.
   - Streamlit Operator Terminal (Tab 10):
     - Adds "Live Execution Ledger & Slippage Attribution" table and scorecard cards.

6. **Comprehensive Test Suite (`tests/unit/test_phase13_live_ledger.py`):**
   - Unit tests for `LiveLedgerManager` journaling and aggregations.
   - Unit tests for `SlippageAttributionEngine` friction calculations and sandwich detection.
   - Unit tests for `WalkForwardValidator` comparative divergence checks.
   - Unit tests for `LiveScorecard` rolling metrics and Go/No-Go decision logic.
   - Zero test regressions across existing 229 tests.
