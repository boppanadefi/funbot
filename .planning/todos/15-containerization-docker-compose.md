---
id: todo-15-containerization-docker-compose
title: Containerization, Dockerfile & docker-compose setup
area: devops
status: pending
created: 2026-10-18
priority: medium
tags: [docker, container, compose, deployment, env]
---

# Containerization, Dockerfile & Docker-Compose Setup

## Objective
Provide containerized execution manifests for non-stop autonomous operations on Linux/Cloud VMs.

## Context & Requirements
- Create multi-stage `Dockerfile`:
  - Base: lightweight `python:3.11-slim` or `python:3.14-slim`.
  - Layer caching for dependencies.
  - Zero C-compiler requirement (pure Python Ed25519 and compact-u16).
  - Non-root runtime user for security isolation.
- Create `docker-compose.yml`:
  - Service 1: `funbot-daemon` running `MasterTradingOrchestrator`.
  - Service 2: `funbot-terminal` running Streamlit dashboard on port `8501`.
  - Shared volume mounts for `data/telemetry/` SQLite WAL database and parquets.
  - Restart policy `unless-stopped`.
- Create `.env.example`:
  - Document all mandatory and optional settings (`HELIUS_API_KEY`, `HELIUS_RPC_URL`, `HELIUS_WSS_URL`, `DISCORD_WEBHOOK_URL`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `SIMULATION_MODE`).

## Verification
- Validate `docker compose config` passes syntax validation.
- Verify container entry point runs without path or environment errors.
