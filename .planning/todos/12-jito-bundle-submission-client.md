---
id: todo-12-jito-bundle-submission-client
title: Jito bundle submission client
area: mev
status: pending
created: 2026-10-18
priority: critical
tags: [jito, mev, bundles, block-engine, tip-optimization]
---

# Jito Bundle Submission Client

## Objective
Implement a production-grade Jito MEV Block Engine client with multi-region failover, dynamic tip adaptation, and bundle status polling.

## Context & Requirements
- Multi-Region Block Engine Endpoints:
  - Default: `https://mainnet.block-engine.jito.wtf/api/v1/bundles`
  - Regional fallbacks: Amsterdam, Frankfurt, New York, Tokyo.
- Dynamic Tip Optimization:
  - Query Jito tip stream / API for p50 and p75 tip percentiles.
  - Enforce Guardrail 6: Tip must never exceed 1.5% notional cap.
  - Ensure tip instruction is placed strictly AFTER the swap instruction.
- Bundle Status Tracking:
  - Call `getBundleStatuses` to track whether the bundle was landed in a block, invalid, or dropped.
  - If dropped, evaluate resubmission within 1 block window.

## Verification
- Unit test bundle payload construction, tip cap clamping, and mock status responses.
