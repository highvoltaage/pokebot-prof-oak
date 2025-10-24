# AGENTS.md
# Operational Guidelines for AI Agents working in this repository

This repository extends **40cakes/pokebot-gen3** with a dedicated plugin, **Prof Oak Mode**, that tracks and enforces shiny quotas for "Prof Oak" and "Living Prof Oak" playthroughs.  
This document defines how AI agents (e.g., Codex) should behave when reading, editing, reviewing, or proposing changes within this repository.

---

## 0. Core Principles

- 🧠 **Review-First Policy:**  
  Codex must always perform a full read and analysis of all plugin files before proposing any code changes.  
  It must generate a structured `Execution Plan` in `PLANS.md`, explaining what it wants to change, why, and how it justifies that the change will work.  
  No code edits are allowed until that plan is approved.

- 🔒 **Safety Boundaries:**  
  Codex **must never** modify or delete anything under the `40cakes/` directory.  
  That directory exists solely as a reference for upstream logic and is considered **read-only**.

- 🧩 **Focus Area:**  
  Codex may edit or create files under:
  - `plugins/ProfOak/`
  - `plugins/prof_oak_mode.py`
  - any support or metadata files (e.g., `VERSION.json`, `AGENTS.md`, `PLANS.md`)
  - documentation (`README.md`, etc.)

---

## 1. Environment and Setup

### Python Version
Use **Python 3.12**.

### Dependency Management
The bot automatically handles dependencies by running its own `requirements` and `updater.py` routines on launch.  
Codex should **not** manually install or modify dependencies.

### Launch Command
