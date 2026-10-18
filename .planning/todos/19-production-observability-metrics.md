---
id: todo-19-production-observability-metrics
title: Structured logging & Prometheus metrics integration
area: telemetry
status: pending
created: 2026-10-18
priority: medium
tags: [telemetry, observability, logging, prometheus, metrics]
---

# Structured Logging & Prometheus Metrics Integration

## Objective
Implement structured JSON logging and Prometheus metrics exposition for long-running daemon monitoring and alerting.

## Context & Requirements
- Structured Logging:
  - Format logs with standard timestamp, severity, subsystem (`engine`, `orchestrator`, `live`, `jito`), and structured context dicts.
  - Console renderer for human operators; JSON formatter for log aggregators (e.g., Datadog, Grafana Loki).
- Prometheus Metrics Exporter:
  - Expose `/metrics` HTTP endpoint or background collector.
  - Gauges: active positions, sub-wallet balance, daily PnL, circuit breaker status.
  - Counters: swap events processed, candidates evaluated, orders submitted, orders confirmed, Jito bundles dispatched.
  - Histograms: Helius RPC latency, Jito bundle landing latency, WebSocket tick delay.

## Verification
- Verify scraping `/metrics` returns valid OpenMetrics / Prometheus format.
