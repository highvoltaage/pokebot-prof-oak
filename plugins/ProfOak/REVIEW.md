# PokéBot Gen3 Codebase Overview

## Core runtime (40cakes/pokebot)
- **Entrypoint & startup flow.** `pokebot.py` parses command-line flags, loads plugins, selects a profile, and boots either the GUI or headless harness before handing control to `modules.main.main_loop`.【F:40cakes/pokebot.py†L1-L161】
- **Global context singleton.** `modules.context` initialises profile configuration, carries shared emulator state (GUI, current bot mode, listeners, etc.), and exposes helpers that plugins can query or mutate during play.【F:40cakes/modules/context.py†L17-L188】
- **Plugin surface area.** `modules.plugin_interface.BotPlugin` defines opt-in hooks for registering additional bot modes/listeners and reacting to lifecycle events such as battles, encounters, or profile loads—your plugin subclasses this contract.【F:40cakes/modules/plugin_interface.py†L11-L188】

## Existing Prof Oak plugin pieces
- **Mode wrapper (`plugins/prof_oak_mode.py`).** Persists the chosen base mode, surfaces “Prof Oak” and “Living Prof Oak” variants, and toggles the shiny quota plugin’s living-dex behaviour whenever the wrapped mode starts running.【F:plugins/prof_oak_mode.py†L1-L344】
- **Capability snapshot (`plugins/ProfOak/capabilities.py`).** Reads badges, key items, and HM move knowledge defensively across forks, returning a cached `Capabilities` dataclass with flags for rods/bikes and traversal moves (Surf, Fly, Rock Smash, etc.).【F:plugins/ProfOak/capabilities.py†L1-L381】
- **Navigator scaffold (`plugins/ProfOak/navigator.py`).** Loads the Emerald route order, resolves the current map index, inspects map tiles to find viable encounter spots per encounter method, and abstracts emulator control helpers behind defensive logging.【F:plugins/ProfOak/navigator.py†L1-L200】
- **Shiny quota extension (`plugins/shiny_quota.py`).** Tracks learned encounters, owned shinies, Unown letters, and per-map encounter tables while coordinating with the navigator to pause or travel once quotas are complete; supports Living Dex toggles and defers backlog computation until capabilities are known.【F:plugins/shiny_quota.py†L1-L200】【F:plugins/shiny_quota.py†L1040-L1100】

## Follow-up issues spotted
1. **Route order JSON path casing.** The navigator hardcodes a lower-case `json` directory, but your data ship under `JSON`, so `_load_route_order` fails on case-sensitive systems and navigation never activates.【F:plugins/ProfOak/navigator.py†L51-L61】
2. **Missing capability helper.** `_get_current_capabilities` in `shiny_quota.py` attempts to import `get_current_capabilities` from the capabilities module, yet only `compute_capabilities`, `refresh_capabilities`, and `get_cached_capabilities` exist. The call always falls back to `{"GRASS"}`, causing backlog filters to ignore Surf/Rod/Rock Smash unlocks.【F:plugins/ProfOak/capabilities.py†L302-L381】【F:plugins/shiny_quota.py†L1052-L1061】

