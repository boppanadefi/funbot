# Funbot Master Todo Registry & Phase Mapping

This directory contains the structured tasks, ideas, and system components captured under `/gsd-add-todo`, mapped across the Funbot lifecycle ([ROADMAP.md](file:///c:/Users/sivab/Documents/products/funbot/.planning/ROADMAP.md)).

---

## Active & Infrastructure Backlog (Milestone 3 & Beyond)

| ID | Title | Area | Priority | Status | Tags |
|---|---|---|:---:|:---:|---|
| [todo-14](file:///c:/Users/sivab/Documents/products/funbot/.planning/todos/14-python-packaging-init-pyproject.md) | **Python packaging, missing `__init__.py` files & `pyproject.toml`** | `infrastructure` | HIGH | `COMPLETED` | `packaging`, `pyproject`, `init`, `dependencies` |
| [todo-15](file:///c:/Users/sivab/Documents/products/funbot/.planning/todos/15-containerization-docker-compose.md) | **Containerization, Dockerfile & docker-compose setup** | `devops` | MEDIUM | `COMPLETED` | `docker`, `compose`, `deployment`, `env` |
| [todo-16](file:///c:/Users/sivab/Documents/products/funbot/.planning/todos/16-root-cli-entry-point.md) | **Unified root CLI entry point (`main.py` / `__main__.py`)** | `core` | HIGH | `COMPLETED` | `cli`, `entrypoint`, `main`, `runner`, `daemon` |
| [todo-17](file:///c:/Users/sivab/Documents/products/funbot/.planning/todos/17-project-directory-scaffolding-docs.md) | **Directory scaffolding, documentation & test structure** | `infrastructure` | MEDIUM | `COMPLETED` | `directories`, `scaffolding`, `docs`, `fixtures` |
| [todo-18](file:///c:/Users/sivab/Documents/products/funbot/.planning/todos/18-property-based-testing-hypothesis.md) | **Property-based testing for AMM constant-product math** | `testing` | MEDIUM | `PENDING` | `testing`, `property-based`, `hypothesis`, `amm` |
| [todo-19](file:///c:/Users/sivab/Documents/products/funbot/.planning/todos/19-production-observability-metrics.md) | **Structured logging & Prometheus metrics integration** | `telemetry` | MEDIUM | `PENDING` | `telemetry`, `observability`, `logging`, `metrics` |

---

## Solana-Specific Competitive Enhancements & Advanced Defense Backlog (Todos 20–30)

| ID | Title | Area | Priority | Status | Tags |
|---|---|---|:---:|:---:|---|
| [todo-20](file:///c:/Users/sivab/Documents/products/funbot/.planning/todos/20-lifecycle-simulation-honeypot-detection.md) | **Lifecycle Simulation & Honeypot Detection (Check 13)** | `engine` | CRITICAL | `PENDING` | `simulation`, `honeypot`, `entry-gate`, `checks`, `execution` |
| [todo-21](file:///c:/Users/sivab/Documents/products/funbot/.planning/todos/21-token2022-threat-checks-extensions.md) | **Token-2022 Threat Checks & Extension Audit (Check 14)** | `security` | CRITICAL | `PENDING` | `token2022`, `transfer-fee`, `security`, `extensions`, `entry-gate` |
| [todo-22](file:///c:/Users/sivab/Documents/products/funbot/.planning/todos/22-program-upgradeability-check-bpf.md) | **Program Upgradeability Check (Check 15)** | `security` | HIGH | `PENDING` | `security`, `bpf`, `upgradeability`, `immutability`, `entry-gate` |
| [todo-23](file:///c:/Users/sivab/Documents/products/funbot/.planning/todos/23-cpi-dependency-audit-whitelist.md) | **CPI Dependency Audit & Program Whitelisting (Check 16)** | `security` | HIGH | `PENDING` | `security`, `cpi`, `bpf`, `bytecode`, `whitelist`, `entry-gate` |
| [todo-24](file:///c:/Users/sivab/Documents/products/funbot/.planning/todos/24-prebuilt-emergency-exit-cache.md) | **Pre-Built Emergency Exit Cache for Zero-Delay Rug Response** | `live` | HIGH | `PENDING` | `live`, `emergency-exit`, `cache`, `rug-response`, `zero-delay` |
| [todo-25](file:///c:/Users/sivab/Documents/products/funbot/.planning/todos/25-authority-change-realtime-monitor.md) | **Authority Change Real-Time WebSocket Monitor** | `telemetry` | HIGH | `PENDING` | `telemetry`, `websocket`, `authority`, `rug-pull`, `emergency-exit` |
| [todo-26](file:///c:/Users/sivab/Documents/products/funbot/.planning/todos/26-slot-skip-network-instability-monitor.md) | **Slot Skip & Network Instability Guard** | `telemetry` | MEDIUM | `PENDING` | `telemetry`, `slots`, `solana`, `network-stability`, `circuit-breaker` |
| [todo-27](file:///c:/Users/sivab/Documents/products/funbot/.planning/todos/27-rent-exempt-balance-verification.md) | **Rent-Exempt Balance Verification for Pool & Token Accounts** | `security` | MEDIUM | `PENDING` | `solana`, `rent-exempt`, `balance`, `security`, `entry-gate` |
| [todo-28](file:///c:/Users/sivab/Documents/products/funbot/.planning/todos/28-compute-budget-exhaustion-defense.md) | **Compute Budget Exhaustion Defense & Dynamic CU Optimization** | `execution` | MEDIUM | `PENDING` | `compute-budget`, `simulation`, `execution`, `solana`, `optimization` |
| [todo-29](file:///c:/Users/sivab/Documents/products/funbot/.planning/todos/29-close-account-attack-detection.md) | **Close Account Attack Detection for AMM Pools** | `security` | MEDIUM | `PENDING` | `security`, `close-account`, `pool-authority`, `entry-gate` |
| [todo-30](file:///c:/Users/sivab/Documents/products/funbot/.planning/todos/30-solana-threat-model-evm-bias-report.md) | **Solana Threat Model vs EVM Bias Documentation** | `docs` | LOW | `PENDING` | `docs`, `threat-model`, `solana`, `evm`, `architecture`, `report` |

---

## Completed Milestone 2 Backlog (Phases 6–9)

| ID | Title | Area | Priority | Status | Tags |
|---|---|---|:---:|:---:|---|
| [todo-01](file:///c:/Users/sivab/Documents/products/funbot/.planning/todos/01-pumpswap-program-interface.md) | **PumpSwap program interface (IDL, instructions)** | `execution` | HIGH | `COMPLETED` | `pumpswap`, `idl`, `solana`, `anchor` |
| [todo-08](file:///c:/Users/sivab/Documents/products/funbot/.planning/todos/08-amm-math-sell-quote-simulation.md) | **AMM math / sell quote simulation** | `amm` | HIGH | `COMPLETED` | `amm`, `math`, `constant-product`, `quotes` |
| [todo-07](file:///c:/Users/sivab/Documents/products/funbot/.planning/todos/07-onchain-authority-auditor.md) | **On-chain authority auditor** | `security` | HIGH | `COMPLETED` | `authority`, `audit`, `freeze`, `mint` |
| [todo-04](file:///c:/Users/sivab/Documents/products/funbot/.planning/todos/04-position-lifecycle-state-machine.md) | **Position lifecycle state machine** | `engine` | HIGH | `COMPLETED` | `position`, `state-machine`, `lifecycle` |
| [todo-05](file:///c:/Users/sivab/Documents/products/funbot/.planning/todos/05-entry-decision-orchestrator-12-checks.md) | **Entry decision orchestrator (12 checks)** | `strategy` | CRITICAL | `COMPLETED` | `entry`, `filters`, `12-checks`, `risk` |
| [todo-06](file:///c:/Users/sivab/Documents/products/funbot/.planning/todos/06-exit-timer-system-5-concurrent-timers.md) | **Exit timer system (5 concurrent timers/pos)** | `engine` | HIGH | `COMPLETED` | `exit`, `timers`, `runner`, `tp1` |
| [todo-02](file:///c:/Users/sivab/Documents/products/funbot/.planning/todos/02-transaction-construction-submission.md) | **Transaction construction & submission** | `execution` | HIGH | `COMPLETED` | `transaction`, `serialization`, `solana` |
| [todo-10](file:///c:/Users/sivab/Documents/products/funbot/.planning/todos/10-transaction-confirmation-monitoring.md) | **Transaction confirmation monitoring** | `execution` | HIGH | `COMPLETED` | `confirmation`, `signatures`, `websocket` |
| [todo-12](file:///c:/Users/sivab/Documents/products/funbot/.planning/todos/12-jito-bundle-submission-client.md) | **Jito bundle submission client** | `mev` | CRITICAL | `COMPLETED` | `jito`, `mev`, `bundles`, `block-engine` |
| [todo-13](file:///c:/Users/sivab/Documents/products/funbot/.planning/todos/13-async-architecture-scheduling-framework.md) | **Async architecture / scheduling framework** | `architecture` | HIGH | `COMPLETED` | `asyncio`, `scheduling`, `workers`, `queues` |
| [todo-03](file:///c:/Users/sivab/Documents/products/funbot/.planning/todos/03-main-event-loop-orchestrator.md) | **Main event loop / orchestrator** | `core` | CRITICAL | `COMPLETED` | `orchestrator`, `event-loop`, `asyncio` |
| [todo-09](file:///c:/Users/sivab/Documents/products/funbot/.planning/todos/09-crash-recovery-state-persistence.md) | **Crash recovery / state persistence** | `infrastructure` | HIGH | `COMPLETED` | `persistence`, `sqlite`, `wal`, `recovery` |
| [todo-11](file:///c:/Users/sivab/Documents/products/funbot/.planning/todos/11-alerting-notifications.md) | **Alerting / notifications** | `telemetry` | MEDIUM | `COMPLETED` | `alerts`, `telegram`, `discord`, `webhooks` |
