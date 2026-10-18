# Funbot Master Todo Registry & Phase Mapping

This directory contains the structured tasks, ideas, and system components captured under `/gsd-add-todo`, mapped directly to **Milestone 2: Live Execution Engine & Production Orchestration** ([ROADMAP.md](file:///c:/Users/sivab/Documents/products/funbot/.planning/ROADMAP.md)).

## Phase 6: Protocol & AMM Engine

| ID | Title | Area | Priority | Status | Tags |
|---|---|---|:---:|:---:|---|
| [todo-01](file:///c:/Users/sivab/Documents/products/funbot/.planning/todos/01-pumpswap-program-interface.md) | **PumpSwap program interface (IDL, instructions)** | `execution` | HIGH | `COMPLETED` | `pumpswap`, `idl`, `solana`, `anchor` |
| [todo-08](file:///c:/Users/sivab/Documents/products/funbot/.planning/todos/08-amm-math-sell-quote-simulation.md) | **AMM math / sell quote simulation** | `amm` | HIGH | `COMPLETED` | `amm`, `math`, `constant-product`, `quotes` |
| [todo-07](file:///c:/Users/sivab/Documents/products/funbot/.planning/todos/07-onchain-authority-auditor.md) | **On-chain authority auditor** | `security` | HIGH | `COMPLETED` | `authority`, `audit`, `freeze`, `mint` |

---

## Phase 7: Strategy & State Engine

| ID | Title | Area | Priority | Status | Tags |
|---|---|---|:---:|:---:|---|
| [todo-04](file:///c:/Users/sivab/Documents/products/funbot/.planning/todos/04-position-lifecycle-state-machine.md) | **Position lifecycle state machine** | `engine` | HIGH | `COMPLETED` | `position`, `state-machine`, `lifecycle` |
| [todo-05](file:///c:/Users/sivab/Documents/products/funbot/.planning/todos/05-entry-decision-orchestrator-12-checks.md) | **Entry decision orchestrator (12 checks)** | `strategy` | CRITICAL | `COMPLETED` | `entry`, `filters`, `12-checks`, `risk` |
| [todo-06](file:///c:/Users/sivab/Documents/products/funbot/.planning/todos/06-exit-timer-system-5-concurrent-timers.md) | **Exit timer system (5 concurrent timers/pos)** | `engine` | HIGH | `COMPLETED` | `exit`, `timers`, `runner`, `tp1` |

---

## Phase 8: Execution & MEV Pipeline

| ID | Title | Area | Priority | Status | Tags |
|---|---|---|:---:|:---:|---|
| [todo-02](file:///c:/Users/sivab/Documents/products/funbot/.planning/todos/02-transaction-construction-submission.md) | **Transaction construction & submission** | `execution` | HIGH | `COMPLETED` | `transaction`, `serialization`, `solana` |
| [todo-10](file:///c:/Users/sivab/Documents/products/funbot/.planning/todos/10-transaction-confirmation-monitoring.md) | **Transaction confirmation monitoring** | `execution` | HIGH | `COMPLETED` | `confirmation`, `signatures`, `websocket` |
| [todo-12](file:///c:/Users/sivab/Documents/products/funbot/.planning/todos/12-jito-bundle-submission-client.md) | **Jito bundle submission client** | `mev` | CRITICAL | `COMPLETED` | `jito`, `mev`, `bundles`, `block-engine` |

---

## Phase 9: Orchestrator & Production Reliability

| ID | Title | Area | Priority | Status | Tags |
|---|---|---|:---:|:---:|---|
| [todo-13](file:///c:/Users/sivab/Documents/products/funbot/.planning/todos/13-async-architecture-scheduling-framework.md) | **Async architecture / scheduling framework** | `architecture` | HIGH | `COMPLETED` | `asyncio`, `scheduling`, `workers`, `queues` |
| [todo-03](file:///c:/Users/sivab/Documents/products/funbot/.planning/todos/03-main-event-loop-orchestrator.md) | **Main event loop / orchestrator** | `core` | CRITICAL | `COMPLETED` | `orchestrator`, `event-loop`, `asyncio` |
| [todo-09](file:///c:/Users/sivab/Documents/products/funbot/.planning/todos/09-crash-recovery-state-persistence.md) | **Crash recovery / state persistence** | `infrastructure` | HIGH | `COMPLETED` | `persistence`, `sqlite`, `wal`, `recovery` |
| [todo-11](file:///c:/Users/sivab/Documents/products/funbot/.planning/todos/11-alerting-notifications.md) | **Alerting / notifications** | `telemetry` | MEDIUM | `COMPLETED` | `alerts`, `telegram`, `discord`, `webhooks` |
