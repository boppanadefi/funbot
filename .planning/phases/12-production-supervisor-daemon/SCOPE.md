# Phase 12: Production Supervisor & Daemon Management — Scope

**Phase:** 12-production-supervisor-daemon  
**Milestone:** Milestone 3 — Autonomous Mainnet Live Execution & Micro-Capital Trading (v4.0)  
**Execution Mode:** Production Supervisor & Background Process Lifecycle Management  
**Target Delivery:** Dual-Layer Python Supervisor + Heartbeat Watchdog + Reboot WAL/SPL Reconciliation + OS Service Templates + Test Suite  

---

## Objectives

1. **Dual-Layer Production Supervisor (`src/orchestrator/supervisor.py`):**
   - Autonomous supervisor process managing the trading daemon child (`python main.py daemon`).
   - Crash loop prevention: Exponential restart backoff (5s, 10s, 20s, up to 60s) with max 5 restarts within a 15-minute sliding window before tripping an alert and halting to protect capital.
   - Graceful termination: Propagates termination signals (SIGINT, SIGTERM, CTRL+C/CTRL+BREAK) to child process with 15-second grace period for orderly WAL state flushing before force-killing.
   - Multi-channel notification: Dispatches Discord and Telegram alerts on supervisor start, daemon crash, auto-restart, and crash loop tripping.

2. **Heartbeat Publisher & Resource Watchdog (`src/orchestrator/heartbeat.py`):**
   - **Heartbeat Publisher:** Daemon writes periodic pulses every 30 seconds to `data/telemetry/heartbeat.json` and `data/telemetry/orchestrator_state.db`.
   - Payload includes: `timestamp`, `pid`, `uptime_seconds`, `memory_rss_mb`, `cpu_percent`, `active_positions`, `kill_switch_tripped`, `circuit_breaker_halted`, `last_slot`.
   - **Watchdog Engine:**
     - Stale Heartbeat Detector: Flags heartbeat as stale if older than 60 seconds (2 missed cycles) and triggers automated restart.
     - Memory Leak Guard: Monitors child process RSS memory. If RSS exceeds 512 MB ceiling, dispatches warning alert, initiates graceful restart, and reclaims memory.

3. **Reboot WAL & On-Chain SPL Reconciliation (`src/orchestrator/reconciliation.py`):**
   - On daemon startup/recovery, query active wallet token accounts via Helius RPC (`getTokenAccountsByOwner`).
   - Reconcile on-chain token balances against positions in SQLite WAL `persisted_positions`:
     - **Matching Balance:** Rehydrate position state machine and resume exit timers with accurate remaining lifespan.
     - **Ghost Position (WAL active, 0 on-chain tokens):** Detect out-of-band liquidation during downtime, transition position to CLOSED, and log discrepancy.
     - **Orphan Token (On-chain tokens exist, no active WAL record):** Mark as unmanaged/orphaned asset, log warning, and notify operator.

4. **Production OS Service Templates (`deploy/`):**
   - **Windows:** PowerShell script for Windows Task Scheduler / NSSM service configuration (`deploy/windows/funbot-supervisor.ps1`, `deploy/windows/install_service_nssm.bat`).
   - **Linux / VPS:** Standard systemd unit configuration (`deploy/systemd/funbot-supervisor.service`) with auto-restart on failure.

5. **CLI & Streamlit Live Terminal Integration:**
   - CLI subcommands in `main.py`: `python main.py supervisor [--start | --status | --stop | --watch]`.
   - Streamlit Terminal (Tab 10): Live Supervisor & Heartbeat status banner showing daemon PID, uptime, memory consumption, last heartbeat timestamp, and pulse health indicator.

6. **Comprehensive Automated Test Suite (`tests/unit/test_phase12_supervisor.py`):**
   - Unit tests for `HeartbeatPublisher` and `HeartbeatMonitor`.
   - Unit tests for `ProductionSupervisor` process management, crash detection, and backoff limits.
   - Unit tests for memory threshold detection and stale heartbeat triggering.
   - Unit tests for SPL on-chain reconciliation against WAL state.
   - Zero test regressions across existing 214 tests.
