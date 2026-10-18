# Phase 11: Real-Time Telemetry & Operator Webhooks — Scope

**Phase:** 11-real-time-telemetry-webhooks  
**Milestone:** Milestone 3 — Autonomous Mainnet Live Execution & Micro-Capital Trading (v4.0)  
**Execution Mode:** Simulation & Live Alert Dispatch (Zero Real Funds at Risk)  
**Target Delivery:** Discord Zinc Webhook Engine + Interactive Telegram Operator Bot + Streamlit Live WAL Terminal Tab + Automated Test Suite  

---

## Objectives

1. **Discord Zinc Webhook Live Dispatcher:**
   - Deliver rich visual Discord embeds styled with the dark Zinc aesthetic (`#09090b` / `#18181b` colors, branded badges, Solscan link buttons).
   - Dedicated event payload generators for the full trading lifecycle:
     - `ENTRY_EXECUTED`: Token mint, pool address, entry price, dynamic position size ($3.00–$25.00), expected friction %, liquidity reserves, 12-Check Gate status.
     - `RATCHET_ADJUSTED`: Peak price achieved, new ratchet floor level, guaranteed locked-in gross %.
     - `TP1_SCALE_OUT`: 90% scale-out realization, net SOL, remaining 10% runner status.
     - `POSITION_CLOSED`: Final exit reason (TP1, Structure Stop, Hard Time Kill, Liquidity Collapse), net return %, net PnL in USD.
     - `RISK_HALT_TRIPPED`: Emergency Kill Switch or -$10.00 Daily Circuit Breaker trigger details, requiring operator sign-off.

2. **Interactive Telegram Operator Bot:**
   - Bidirectional operator control bot operating asynchronously in non-blocking event loop.
   - Interactive commands:
     - `/status`: Engine state, active positions count, circuit breaker remaining budget ($10.00 ceiling), bankroll allocation ($25 active, $75 cold reserve), Helius slot & latency, fastest Jito region.
     - `/kill`: Instantly trips `EmergencyKillSwitch` with audit trail ("Telegram operator remote panic stop").
     - `/reset`: Clears the emergency kill switch following authenticated operator sign-off.
     - `/circuit_breaker`: Checks daily loss tally, trade count, and reset status.
     - `/positions`: Real-time inspection of open positions, current prices, stop loss levels, and unrealized PnL.
     - `/help`: Operator guide to available commands.
   - Security: Enforces strict whitelist of authorized operator chat IDs (`TELEGRAM_ALLOWED_CHAT_IDS`). Rejects unauthorized interactions with security alerts.

3. **Streamlit Live Terminal Real-Time SQLite WAL Streaming:**
   - Dedicated "10. Live Operator Terminal" tab within the Streamlit dashboard (`src/ui/app.py`).
   - Non-blocking real-time polling of SQLite WAL journals (`telemetry.db` and `orchestrator_state.db`).
   - Real-time auto-refresh toggle with configurable polling frequency (2s, 5s, 10s).
   - Interactive operator control buttons: Emergency Panic Stop (`Trip Kill Switch`), `Reset Kill Switch`, and `Reset Circuit Breaker`.
   - Live visual feed of recent alert events from `AlertDispatcher` with severity indicators and timestamps.
   - Live position monitor widget displaying active positions with real-time unrealized return and trailing ratchet tracking.

4. **Orchestrator Integration & Automated Test Suite:**
   - Connect `AlertDispatcher` and `TelegramOperatorBot` into `MasterTradingOrchestrator` lifecycle and `main.py` CLI.
   - Comprehensive test suite in `tests/unit/test_phase11_telemetry.py` covering embed builders, command parsers, security filters, WAL log streamers, and mocked network deliveries.
