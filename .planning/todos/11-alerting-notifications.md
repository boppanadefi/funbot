---
id: todo-11-alerting-notifications
title: Alerting / notifications
area: telemetry
status: completed
created: 2026-10-18
completed: 2026-10-18
priority: medium
tags: [alerts, telegram, discord, notifications, monitoring]
---

# Alerting / Notifications

## Objective
Establish an asynchronous alerting and notification subsystem to broadcast real-time operational events, risk triggers, and execution receipts to Discord / Telegram webhooks.

## Context & Requirements
- Alert Channels:
  - Telegram Bot API webhook (`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` in `.env`).
  - Discord Webhook URL (`DISCORD_WEBHOOK_URL` in `.env`).
  - Terminal stderr / UI toast notifications.
- Alert Event Types:
  - `TRADE_ENTRY`: Token mint, entry price, position size ($/SOL), VUW score.
  - `TP1_FILLED`: 90% position closed at target, realized profit, runner activated.
  - `RUNNER_EXIT`: Runner closed by structure stop or 3-minute time kill.
  - `CIRCUIT_BREAKER_HALT`: Daily net loss reached -$10.00 ceiling, all trading halted.
  - `KILL_SWITCH_ENGAGED`: Manual operator kill switch triggered.
  - `RPC_STALENESS_WARNING`: Feed latency exceeded 1,000ms threshold.
- Rate Limiting: Coalesce rapid alerts to avoid API rate limits.

## Implementation Details
- Delivered `AlertDispatcher`, `AlertEvent`, and `AlertSeverity` in `src/orchestrator/alerting.py`.
- Discord embeds with Zinc styling and color coding.
- Telegram markdown formatter with icon badges.
- Token-bucket rate limiter (30 alerts/minute).
- Verified via `tests/unit/test_phase9_orchestrator.py` (all tests passing).
