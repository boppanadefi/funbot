---
id: todo-14-python-packaging-init-pyproject
title: Python packaging, missing __init__.py files & pyproject.toml
area: infrastructure
status: pending
created: 2026-10-18
priority: high
tags: [packaging, pyproject, init, dependencies]
---

# Python Packaging, Missing `__init__.py` Files & `pyproject.toml`

## Objective
Establish modern standard Python packaging metadata (`pyproject.toml`) and ensure all source directories contain proper `__init__.py` markers.

## Context & Requirements
- Add missing `__init__.py` markers to:
  - `src/__init__.py`
  - `src/telemetry/__init__.py`
  - `src/ui/__init__.py`
  - `src/ui/components/__init__.py`
  - `src/utils/__init__.py`
- Create `pyproject.toml`:
  - Build system definition (`setuptools` / `flit_core`).
  - Project metadata (name: `funbot`, version: `4.0.0`, description, python requires `>= 3.11`).
  - Core dependencies and optional dev dependency groups (`test`: pytest, hypothesis; `lint`: ruff, mypy).
  - Tool configurations for pytest, ruff, and coverage.
- Verify `python -m pytest` and direct module imports work seamlessly across all subpackages.

## Verification
- Verify `import src.telemetry`, `import src.ui.components`, `import src.utils` resolve cleanly.
- Verify `pytest` runs and recognizes package metadata without warning.
