# Prof Oak for PokéBot (Gen 3)

A WIP plugin set for [PokeBot-Gen3](https://github.com/PokeBot-Gen3/PokeBot-Gen3) that adds “Prof Oak” style shiny-hunting with **route quotas** and an optional **Living Dex** variant.

---

## Contents

```
plugins/
├─ prof_oak_mode.py             # Registers “Prof Oak” and “Living Prof Oak” modes (wraps a base mode like Spin/LevelGrind)
└─ ProfOak/
   ├─ shiny_quota.py            # Quota logic (learn per-route encounters, count owned shinies, act on quota)
   └─ json/                     # (runtime) learned/owned JSONs written here
```

> No static JSONs are committed. The plugin learns encounters at runtime and writes data under `plugins/ProfOak/json/`.

---

## Features

- **Learns-as-you-go:** tracks species you actually encounter per **(map, method)** using `EncounterInfo`.
- **PC + Party scan for owned shinies:** counts real shinies from storage & party — no screenshots or manual lists.
- **Two hunt modes**
  - **Prof Oak:** requires *one* shiny per evolutionary line (any family member satisfies the line).
  - **Living Prof Oak:** requires one shiny per *evolution stage* (handles branching families like Wurmple).
- **On-quota action:** switch to **Manual** now; **Navigate** is designed (navigator module stub hook included).
- **Fork-friendly & defensive:** plays nice with Emerald/FRLG timing and optional imports; won’t crash if something’s missing.

---

## Install

1. Copy these files into your bot folder:
   - `plugins/prof_oak_mode.py`
   - `plugins/shiny_quota.py`
2. Start the bot and pick:
   - **Prof Oak**
   - **Living Prof Oak** (will flip the living-dex flag inside `ShinyQuota`)

> The plugin will create `plugins/ProfOak/` automatically and store runtime data there.

---

## Usage

1. Choose a farming method (grass, rod, surf, etc.) and hunt normally.
2. The plugin:
   - records encountered species for the current **map + method**
   - scans **PC + Party** to count owned shinies
3. When the route quota is met, the plugin performs your **On-Quota** action (Manual by default).

You’ll see periodic console lines like:
```
[ShinyQuota] Standard missing (3): PIDGEY×1, RATTATA×1, SPEAROW×1…
```

---

## Configuration

All config is inline and easy to tweak near the top of each file.

### `plugins/ProfOak/shiny_quota.py`

- `ON_QUOTA = "manual"`  
  Behavior when quota is met:
  - `"manual"` → switch the bot to Manual mode
  - `"navigate"` → try to call a navigator at `plugins/ProfOak/navigator.py`
- `GROUP_FISHING_WITH_WATER = False`  
  If `True`, group rod encounters with SURF/WATER; else use a separate `ROD` method.
- `LIVING_DEBUG`, `DEBUG_DUMP`  
  Verbose console output for tuning.
- (Optional) catch-block integration is present behind a flag; defaults off.

### `plugins/prof_oak_mode.py`

- `PLUGIN_DEFAULT_BASES = ["Spin", "LevelGrind"]`  
  Which base mode(s) to try to wrap (first found wins).
- `ASK_ON_FIRST_USE = False`  
  If `True` and running in a TTY, prompt once to pick the base mode and save it.

> The Prof Oak modes will ensure `ShinyQuota` is registered and keep its **living-dex** flag in sync with the chosen mode.

---

## How it works

- **Key** = `(map, method)` where:
  - `map` is the map/area name from `EncounterInfo.map.name`
  - `method` is normalized from `EncounterInfo.type` (e.g., `GRASS`, `ROD`, `SURF`, `ROCK_SMASH`, `STATIC`, `SAFARI`)
- **Learned species** per key are stored at:
  - `plugins/ProfOak/json/emerald_learned_by_mapmode.json`
- **Owned shinies** (PC + Party) snapshot is stored at:
  - `plugins/ProfOak/json/owned_shinies.json`
- **Prof Oak (Standard):** requires 1 shiny per evolutionary family
- **Living Prof Oak:** requires 1 shiny per evolution stage across the whole family (branching supported)

When quota is met:
- If `ON_QUOTA = "manual"` → switch to Manual mode (safest default)
- If `ON_QUOTA = "navigate"` → call a function in `plugins/ProfOak/navigator.py`:
  - `navigate_after_quota(context, current_map, method, learned, owned_counts)` **or**
  - `navigate_to_next_target(context, current_map, method, learned, owned_counts)`
  - If missing or errors, it falls back to Manual.

---

## Current WIP / Roadmap

- **Navigator:** call into the bot’s overworld pathing to travel to the next route based on progression flags and a route-order JSON.
- **Static encounter data (optional):** later we may ship prebuilt encounter JSONs (e.g., from pret decomp) to bootstrap learning.
- **Versioning / update helper:** simple version file + publish script (local script already prototyped).
- **Auto catch-block:** add completed species automatically to `profiles/catch_block.yml` (implemented, but not yet tested).

---

## Contributing

Issues & PRs welcome. Please avoid committing ROMs or extracted copyrighted assets.

---

## License

MIT
