# Prof Oak Mode for PokéBot Gen3

Prof Oak Mode extends the [40cakes/pokebot-gen3](https://github.com/40cakes/pokebot-gen3) project with tooling for "Professor Oak" and "Living Dex" style playthroughs. The plugin enforces per-route shiny quotas, manages encounter backlogs, and can optionally steer the bot along a curated story-safe route order.

## Prerequisites
- A working checkout of `40cakes/pokebot-gen3` (see `40cakes/README.md` for emulator and firmware requirements).
- Python 3.12 with the dependencies installed by the upstream bot's bootstrap scripts.
- A supported Generation III ROM and a save file profile recognised by PokéBot Gen3.

## Installation
1. Clone or download this repository alongside your existing PokéBot Gen3 checkout.
2. Copy the `plugins/` directory into the root of your PokéBot installation, or add this repository to `PYTHONPATH` so the modules can be imported directly.
3. Launch the bot with `python pokebot.py --plugins prof_oak_mode` (or enable the plugin in your profile configuration).
4. Pick the base mode(s) that Prof Oak Mode should wrap the first time it runs; the selection persists to `plugins/ProfOak/config.json`.

> **Note:** Everything in the `40cakes/` folder is provided for reference only—do not modify those files. All plugin configuration lives under `plugins/ProfOak/`.

## Key Features
### Shiny quota tracking
- `plugins/ProfOak/shiny_quota.py` mirrors the upstream shiny quota plugin while adding Professor Oak specific constraints.
- Requirements now expand Unown encounters into only the letters available for the active chamber, using the runtime cache stored at `plugins/ProfOak/JSON/unown_letters_seen.json`.
- The owned shiny cache refreshes immediately after a catch so Emerald/Sapphire/Ruby quotas update without restarting the plugin.

### Living Prof Oak Mode
- Toggle the "Living" variant from the mode selection prompt or by editing `plugins/prof_oak_mode.py`.
- When enabled, the plugin tracks individual species ownership and adjusts quotas to require keeping one shiny of each evolutionary line.

### Optional auto-navigation (experimental)
- `plugins/ProfOak/navigator.py` can advance the bot through a curated Emerald route order once a route's quota is complete.
- Navigation and overworld pathing are high-risk. Review `plugins/ProfOak/emerald_route_order.json` and accompanying logic before modifying movement routines.

## JSON Assets
Runtime data is stored alongside the plugin under `plugins/ProfOak/JSON/`:
- `unown_letters_seen.json` — observed Unown forms per map, used to scope active quotas.
- `owned_shinies.json` — living dex cache of owned shinies.

Back up these files if you maintain multiple saves so progress transfers cleanly between sessions.

## Configuration Tips
- Adjust default wrapped modes in `plugins/prof_oak_mode.py` via the `PLUGIN_DEFAULT_BASES` constant or the `PROFOAK_BASE` environment variable.
- The plugin surfaces a one-time prompt (`ASK_ON_FIRST_USE = True`) if you prefer to choose the base modes interactively.
- Capability detection (`plugins/ProfOak/capabilities.py`) reads badges, key items, and traversal HMs defensively so navigation and backlog filters respect story progress.

## Contributing
1. Review `AGENTS.md` for repository guidelines.
2. Document intended changes in `PLANS.md` before editing code.
3. Run `python -m compileall plugins/ProfOak` to catch syntax errors before opening a pull request.
4. Keep JSON assets in the `plugins/ProfOak/JSON/` directory so they load correctly on case-sensitive filesystems.

