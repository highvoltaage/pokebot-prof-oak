# Prof Oak for PokéBot (Gen 3)

**Two drop-in plugins** for PokéBot-style Emerald shiny hunting:
- `shiny_quota.py` — learns encountered species per **(map_enum, MODE)** and **pauses** once you own a shiny of each.
- `prof_oak_mode.py` — a thin bot mode wrapper (“**Prof Oak**”) that runs Level Grind behavior while `shiny_quota` handles quotas.

## Features
- Uses `EncounterInfo` (no ROM edits) to key hunts by **map** and **method** (`GRASS`, `WATER`, `ROD`, etc.).
- Counts owned shinies directly from **PC storage + party** **WIP**.
- Generates **local** JSON (`data/emerald_learned_by_mapmode.json`) as you play; nothing is committed to the repo.

## Install
1. Copy both files into your bot’s `plugins/` folder:
   - `plugins/shiny_quota.py`
   - `plugins/prof_oak_mode.py`
2. Start your bot and select mode **“Prof Oak”** (or run with `-m "Prof Oak"`).
3. Play normally — the plugin tracks encounters and pauses when the quota is met.

> **Note:** Do **not** include ROMs or copyrighted assets in this repo.

## How it works
- On battle start, read `EncounterInfo.map` + `EncounterInfo.type` → normalize to a MODE bucket (GRASS/WATER/ROD/…).
- Record encountered species per `(map_enum, MODE)`.
- Scan **PC + party** for shinies; when all learned species are owned shiny → pause.

## Roadmap
- Overworld Navigation -> Auto move to next route
- “Living Prof Oak” variant (require one shiny per evolution stage).
- Move from "Learning mode" to a published encounter file.
- version checking/auto update.

## License
MIT
