# -*- coding: utf-8 -*-
# plugins/shiny_quota.py
# Learn-as-you-go shiny quota + optional Living Dex counting.
# Stores learned encounters and owned shinies under plugins/ProfOak/.
from __future__ import annotations

import json
import os
import time
from typing import Dict, List, Optional, Set, Iterable

from modules.plugin_interface import BotPlugin
from modules.context import context
from modules.runtime import get_base_path
from modules.pokemon import Pokemon

# Optional helpers (fork differences)
try:
    from modules.pokemon_storage import get_pokemon_storage  # type: ignore
except Exception:
    get_pokemon_storage = None  # type: ignore

try:
    from modules.pokemon_party import get_party as _get_party  # type: ignore
except Exception:
    try:
        from modules.pokemon_party import get_pokemon_party as _get_party  # type: ignore
    except Exception:
        _get_party = None  # type: ignore

try:
    from modules.pokemon import get_species_by_name as _get_species_by_name  # type: ignore
except Exception:
    _get_species_by_name = None  # type: ignore
try:
    from modules.pokemon import get_species_by_index as _get_species_by_index  # type: ignore
except Exception:
    _get_species_by_index = None  # type: ignore

PLUGIN_NAME = "ShinyQuota"

# ---------------- CONFIG ----------------
GLOBAL_SPECIES_OWNERSHIP = True
PAUSE_ACTION = "manual"            # "pause" or "manual"
DEBUG_DUMP = False                 # Log EncounterInfo fields at battle start
LIVING_DEBUG = False               # Extra logs for evolution counting
GROUP_FISHING_WITH_WATER = False   # Treat ROD as SURF instead of ROD
# === Catch-block YAML integration ===
AUTO_BLOCK_COMPLETED_SPECIES = True  # set False to disable auto-blocking
CATCH_BLOCK_PATH = get_base_path() / "profiles" / "catch_block.yml"


# ---------------- Paths (under plugins/ProfOak) ----------------
def _profoak_dir():
    return get_base_path() / "plugins" / "ProfOak"

PROFOAK_DIR = _profoak_dir()
LEARNED_PATH = PROFOAK_DIR / "emerald_learned_by_mapmode.json"
OWNED_DB_PATH = PROFOAK_DIR / "owned_shinies.json"

# -------------- tiny logging helpers --------------
def _log_info(msg: str) -> None:
    for attr in ("logger", "log"):
        lg = getattr(context, attr, None)
        if lg and hasattr(lg, "info"):
            try:
                lg.info(msg); return
            except Exception:
                pass
    print(msg)

def _log_warn(msg: str) -> None:
    for attr in ("logger", "log"):
        lg = getattr(context, attr, None)
        if lg and hasattr(lg, "warning"):
            try:
                lg.warning(msg); return
            except Exception:
                pass
        if lg and hasattr(lg, "warn"):
            try:
                lg.warn(msg); return
            except Exception:
                pass
    print(f"WARNING: {msg}")

def _notify(msg: str) -> None:
    try:
        if hasattr(context, "notify") and callable(getattr(context, "notify")):
            context.notify(msg); return
    except Exception:
        pass
    _log_info(f"[{PLUGIN_NAME}] {msg}")

def _status(msg: str) -> None:
    try:
        if hasattr(context, "overlay") and hasattr(context.overlay, "set_status_line"):
            context.overlay.set_status_line(msg)
    except Exception:
        pass

def _emulator_ready() -> bool:
    try:
        emu = getattr(context, "emulator", None)
        gc = getattr(emu, "get_frame_count", None) if emu else None
        if callable(gc):
            _ = gc(); return True
    except Exception:
        return False
    return False

# ---------------- small JSON I/O ----------------
def _read_json(path) -> dict:
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}

def _write_json(path, data: dict) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    except Exception:
        _log_warn(f"Failed to write {path}")

# ======================================================
#                       PLUGIN
# ======================================================
class ShinyQuotaPlugin(BotPlugin):
    name = PLUGIN_NAME
    version = "0.3.3-alpha.0"
    description = "Pause when you have a shiny of every species you've encountered on this map+mode. Supports Living Dex."
    author = "HighVoltaage"

    SUPPORTS_LIVING_DEX = True

    def __init__(self) -> None:
        try:
            PROFOAK_DIR.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass

        self.learned: Dict[str, Dict[str, List[str]]] = _read_json(LEARNED_PATH) or {}

        self.current_map_key: Optional[str] = None
        self.current_mode: str = "GRASS"

        self.livingdex_enabled: bool = False

        self.required_species_current: Set[str] = set()
        self.required_counts_current: Dict[str, int] = {}
        self.owned_species_global: Set[str] = set()
        self.owned_counts_global: Dict[str, int] = {}

        # species graph cache for living-dex fallback
        self._graph_parent_to_children: Optional[Dict[int, List[int]]] = None
        self._all_species_cache: Optional[List[object]] = None

        self._pending_initial_scan = True
        _log_info(f"[{PLUGIN_NAME}] Initialized. Learned @ {LEARNED_PATH.name}, Owned @ {OWNED_DB_PATH.name} (scan deferred)")

    # ---------------- external toggles ----------------
    def set_livingdex_enabled(self, enabled: bool) -> None:
        self.livingdex_enabled = bool(enabled)
        _log_info(f"[{PLUGIN_NAME}] Living Dex {'ENABLED' if self.livingdex_enabled else 'DISABLED'}.")
        self._rebuild_requirements_cache()
        self._update_status()  # ensure overlay message flips immediately

    def set_quota_mode(self, mode: str) -> None:
        m = (mode or '').strip().upper()
        if m == "LIVING":
            self.set_livingdex_enabled(True)
        elif m == "STANDARD":
            self.set_livingdex_enabled(False)

    def force_refresh(self) -> None:
        """Recompute ownership + requirements and refresh status immediately."""
        try:
            if _emulator_ready():
                self._refresh_owned_species_global(write_out=False)
        except Exception:
            pass
        self._rebuild_requirements_cache()
        self._update_status()

    # ---- BotPlugin interface ----
    def get_additional_bot_modes(self) -> Iterable[type]:  # none added
        return ()

    def on_profile_loaded(self, *_args, **_kwargs) -> None:
        if _emulator_ready():
            self._refresh_owned_species_global(write_out=True)
            self._pending_initial_scan = False
        else:
            _log_warn(f"[{PLUGIN_NAME}] Emulator not ready; will scan PC/party on first battle.")
        _log_info(f"[{PLUGIN_NAME}] Profile loaded. Shinies known: {len(self.owned_species_global)} species")

    def on_battle_started(self, encounter=None, *args, **kwargs) -> None:
        try:
            if getattr(self, "_pending_initial_scan", False) and _emulator_ready():
                self._refresh_owned_species_global(write_out=True)
                self._pending_initial_scan = False

            enc = encounter or self._get_encounterinfo(args, kwargs)
            if DEBUG_DUMP:
                self._debug_dump_enc(enc)
            if enc is None:
                _log_warn(f"[{PLUGIN_NAME}] No EncounterInfo payload; cannot learn.")
                return

            map_key = self._map_key_from_enc(enc)
            mode_key = self._normalized_mode_from_enc(enc)
            if not map_key or not mode_key:
                _log_warn(f"[{PLUGIN_NAME}] Missing map/mode on EncounterInfo; skipping learn.")
                return
            self.current_map_key = map_key
            self.current_mode = mode_key

            species = self._species_from_enc(enc)
            if not species:
                _log_warn(f"[{PLUGIN_NAME}] Could not read species at battle start.")
                return

            per_map = self.learned.setdefault(map_key, {})
            cur = set(per_map.get(mode_key, []))
            if species not in cur:
                cur.add(species)
                per_map[mode_key] = sorted(cur)
                _write_json(LEARNED_PATH, self.learned)
                _log_info(f"[{PLUGIN_NAME}] Learned {species} on {map_key} ({mode_key}).")

            self._rebuild_requirements_cache()
            self._update_status()
            self._maybe_pause_if_quota_met()
        except Exception as e:
            _log_warn(f"on_battle_started error: {e}")

    def on_pokemon_caught(self, mon: Pokemon, *args, **kwargs) -> None:
        try:
            if _emulator_ready():
                self._refresh_owned_species_global(write_out=True)
            self._maybe_pause_if_quota_met()
        except Exception as e:
            _log_warn(f"on_pokemon_caught error: {e}")

    # --------------------------------------------------
    # Core
    # --------------------------------------------------
    def _rebuild_requirements_cache(self) -> None:
        if not self.current_map_key:
            return
        per_mode = self.learned.get(self.current_map_key, {})
        species_list = list(per_mode.get(self.current_mode, []))

        if not self.livingdex_enabled:
            self.required_species_current = set(species_list)
            self.required_counts_current.clear()
            return

        # Living Dex: more robust evo counting
        req_counts: Dict[str, int] = {}
        for sname in species_list:
            evo_count = self._count_forward_evolutions(sname)
            need = 1 + max(0, evo_count)
            req_counts[sname] = need
            if LIVING_DEBUG:
                _log_info(f"[{PLUGIN_NAME}] Need for {sname}: {need} (forward evolutions={evo_count})")
        self.required_counts_current = req_counts
        self.required_species_current = set(species_list)

    def _maybe_pause_if_quota_met(self) -> None:
        if not self.current_map_key:
            return

        if not self.livingdex_enabled:
            if not self.required_species_current:
                return
            owned = self.owned_species_global if GLOBAL_SPECIES_OWNERSHIP else set()
            missing = sorted(self.required_species_current - owned)
            self._update_catch_block_if_needed()
            self._update_status()
            if self.required_species_current and not missing:
                self._hit_quota()
            else:
                if missing:
                    _log_info(f"[{PLUGIN_NAME}] Missing {len(missing)} on {self.current_map_key}: {', '.join(missing[:5])}…")
            return

        if not self.required_counts_current:
            return
        deficits = []
        for s, need in self.required_counts_current.items():
            have = self.owned_counts_global.get(s, 0)
            if have < need:
                deficits.append((s, need - have))

        self._update_catch_block_if_needed()
        self._update_status()

        if not deficits:
            self._hit_quota()
        else:
            top = ", ".join(f"{s}×{d}" for s, d in deficits[:5])
            _log_info(f"[{PLUGIN_NAME}] LivingDex missing ({len(deficits)}): {top}…")

    def _hit_quota(self) -> None:
        msg = f"✅ Quota met on {self.current_map_key} ({self.current_mode})."
        _notify(msg)
        try:
            if PAUSE_ACTION == "pause" and hasattr(context, "pause") and callable(getattr(context, "pause")):
                context.pause()
            elif PAUSE_ACTION == "manual" and hasattr(context, "set_manual") and callable(getattr(context, "set_manual")):
                context.set_manual(True)
        except Exception as e:
            _log_warn(f"Could not change bot state: {e}")

    def _update_status(self) -> None:
        try:
            if not self.livingdex_enabled:
                have = len(self.required_species_current & self.owned_species_global)
                total = len(self.required_species_current)
                _status(f"[{PLUGIN_NAME}] {have}/{total} shinies — {self.current_map_key or 'UNKNOWN'} {self.current_mode}")
            else:
                total_need = sum(self.required_counts_current.values())
                total_have = 0
                for s, need in self.required_counts_current.items():
                    total_have += min(self.owned_counts_global.get(s, 0), need)
                _status(f"[{PLUGIN_NAME}] {total_have}/{total_need} (Living) — {self.current_map_key or 'UNKNOWN'} {self.current_mode}")
        except Exception:
            pass

    # --------------------------------------------------
    # OWNERSHIP: PC storage + party (counts)
    # --------------------------------------------------
    def _refresh_owned_species_global(self, write_out: bool = False) -> None:
        if not _emulator_ready():
            _log_warn(f"[{PLUGIN_NAME}] Emulator not ready; skipping PC/party scan for now.")
            return

        owned_set: Set[str] = set()
        owned_counts: Dict[str, int] = {}

        def _bump(name: Optional[str]):
            if not isinstance(name, str) or not name:
                return
            n = name.upper()
            owned_set.add(n)
            owned_counts[n] = owned_counts.get(n, 0) + 1

        # PC storage
        try:
            if get_pokemon_storage:
                storage = get_pokemon_storage()
                for box in storage.boxes:
                    for slot in box.slots:
                        mon = slot.pokemon
                        if getattr(mon, "is_shiny", False):
                            nm = getattr(mon, "species_name", None) \
                                 or getattr(getattr(mon, "species", None), "name", None)
                            _bump(nm)
        except Exception as e:
            _log_warn(f"[{PLUGIN_NAME}] PC scan failed: {e}")

        # Party
        try:
            mons = None
            if _get_party:
                mons = _get_party()
            elif hasattr(context, "party"):
                mons = getattr(context, "party")
            if mons:
                it = getattr(mons, "pokemon", mons)
                for mon in it:
                    if not mon:
                        continue
                    if getattr(mon, "is_shiny", False):
                        nm = getattr(mon, "species_name", None) \
                             or getattr(getattr(mon, "species", None), "name", None)
                        _bump(nm)
        except Exception as e:
            _log_warn(f"[{PLUGIN_NAME}] Party scan failed: {e}")

        self.owned_species_global = owned_set
        self.owned_counts_global = owned_counts

        if write_out:
            try:
                data = {
                    "last_scan_epoch": int(time.time()),
                    "species_counts": dict(sorted(owned_counts.items())),
                }
                _write_json(OWNED_DB_PATH, data)
                _log_info(f"[{PLUGIN_NAME}] Shinies in PC+party: {sum(owned_counts.values())} mons, {len(owned_set)} species (DB updated)")
            except Exception as e:
                _log_warn(f"[{PLUGIN_NAME}] Could not write owned shinies DB: {e}")
        else:
            _log_info(f"[{PLUGIN_NAME}] Shinies in PC+party: {sum(owned_counts.values())} mons, {len(owned_set)} species")


    # ---------------- catch_block.yml helpers ----------------
    def _pretty_species_name(self, s: str) -> str:
        """Title-case using canonical species name if we can, else Title()."""
        try:
            sp = self._lookup_species_by_name(s)
            nm = getattr(sp, "name", None)
            if isinstance(nm, str) and nm:
                return nm
        except Exception:
            pass
        return s.title()

    def _read_catch_block(self) -> list[str]:
        """Minimal YAML reader for profiles/catch_block.yml (block_list only)."""
        items: list[str] = []
        try:
            text = CATCH_BLOCK_PATH.read_text(encoding="utf-8")
            in_block = False
            for line in text.splitlines():
                t = line.rstrip()
                if t.strip().startswith("#"):
                    continue
                if t.strip().startswith("block_list:"):
                    in_block = True
                    continue
                if in_block and t.strip().startswith("- "):
                    items.append(t.strip()[2:].strip())
                elif in_block and t.strip() and not t.startswith(" "):
                    in_block = False
        except FileNotFoundError:
            pass
        except Exception as e:
            _log_warn(f"[{PLUGIN_NAME}] Could not read catch_block.yml: {e}")
        return items

    def _write_catch_block(self, items: list[str]) -> None:
        """Write profiles/catch_block.yml with a simple stable layout."""
        try:
            CATCH_BLOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
            lines = [
                "# See wiki for documentation: https://github.com/PokeBot-Gen3/PokeBot-Gen3/wiki",
                "block_list:",
            ]
            for name in sorted(items, key=str.lower):
                lines.append(f"  - {name}")
            CATCH_BLOCK_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
        except Exception as e:
            _log_warn(f"[{PLUGIN_NAME}] Could not write catch_block.yml: {e}")

    def _completed_species_for_current_mode(self) -> set[str]:
        """Species that are 'complete' given current mode (standard or living)."""
        if not self.current_map_key:
            return set()

        if getattr(self, "livingdex_enabled", False):
            done = set()
            for s, need in self.required_counts_current.items():
                have = self.owned_counts_global.get(s, 0)
                if need > 0 and have >= need:
                    done.add(s)
            return done

        # Standard: one per species
        return {s for s in self.required_species_current if s in self.owned_species_global}

    def _update_catch_block_if_needed(self) -> None:
        """Append any newly completed species to profiles/catch_block.yml."""
        if not AUTO_BLOCK_COMPLETED_SPECIES:
            return

        completed = self._completed_species_for_current_mode()
        if not completed:
            return

        current_items = self._read_catch_block()
        current_upper = {x.upper() for x in current_items}

        to_add = [self._pretty_species_name(s) for s in sorted(completed) if s.upper() not in current_upper]
        if not to_add:
            return

        new_items = current_items + to_add
        self._write_catch_block(new_items)
        _log_info(f"[{PLUGIN_NAME}] Added to catch_block.yml: {', '.join(to_add)}")
    # --------------------------------------------------
    # Evolution helpers (Living Dex)
    # --------------------------------------------------
    def _count_forward_evolutions(self, species_name: str) -> int:
        """Return the number of unique descendant species reachable from this species."""
        root = self._lookup_species_by_name(species_name)
        if root is None:
            if LIVING_DEBUG:
                _log_warn(f"[{PLUGIN_NAME}] Lookup failed for '{species_name}'")
            return 0

        # First try the species.evolutions list if present
        desc: Set[int] = set()

        def _targets_from(spec_obj) -> List[object]:
            out = []
            evos = getattr(spec_obj, "evolutions", None) or []
            for ev in evos:
                idx = None
                for fld in ("species", "species_index", "target_species_index", "target", "to_index", "to"):
                    val = getattr(ev, fld, None)
                    if isinstance(val, int):
                        idx = val; break
                    if hasattr(val, "index"):
                        try:
                            idx = int(getattr(val, "index"))
                            break
                        except Exception:
                            pass
                if idx is None:
                    continue
                sp2 = self._lookup_species_by_index(idx)
                if sp2 is not None:
                    out.append(sp2)
            return out

        stack = _targets_from(root)
        while stack:
            s = stack.pop()
            idx = getattr(s, "index", None)
            if not isinstance(idx, int) or idx in desc:
                continue
            desc.add(idx)
            stack.extend(_targets_from(s))

        if desc:
            return len(desc)

        # Fallback: build a parent->children graph using evolves_from across ALL species
        graph = self._species_graph_parent_to_children()
        r_idx = getattr(root, "index", None)
        if not isinstance(r_idx, int):
            return 0
        seen: Set[int] = set()
        stack = list(graph.get(r_idx, []))
        while stack:
            i = stack.pop()
            if i in seen:
                continue
            seen.add(i)
            stack.extend(graph.get(i, []))

        if LIVING_DEBUG:
            _log_info(f"[{PLUGIN_NAME}] Fallback descendants for {species_name}: {len(seen)}")
        return len(seen)

    def _species_graph_parent_to_children(self) -> Dict[int, List[int]]:
        if self._graph_parent_to_children is not None:
            return self._graph_parent_to_children
        graph: Dict[int, List[int]] = {}
        for sp in self._iter_all_species():
            child = getattr(sp, "index", None)
            parent = getattr(sp, "evolves_from", None)
            if isinstance(child, int) and isinstance(parent, int):
                graph.setdefault(parent, []).append(child)
        self._graph_parent_to_children = graph
        return graph

    def _iter_all_species(self) -> List[object]:
        if self._all_species_cache is not None:
            return self._all_species_cache
        for attr in ("dex_list", "species_list", "pokemon_list", "species", "all_species"):
            arr = getattr(context, attr, None)
            if isinstance(arr, (list, tuple)) and len(arr) > 0:
                self._all_species_cache = list(arr)
                return self._all_species_cache
        names = None
        for attr in ("species_names", "dex_names", "pokemon_names"):
            arr = getattr(context, attr, None)
            if isinstance(arr, (list, tuple)) and arr:
                names = arr; break
        out = []
        if names:
            for i in range(len(names)):
                sp = self._lookup_species_by_index(i)
                if sp is not None:
                    out.append(sp)
        self._all_species_cache = out
        return out

    # --------------------------------------------------
    # Extractors (EncounterInfo-first)
    # --------------------------------------------------
    def _get_encounterinfo(self, args, kwargs):
        for k in ("encounter", "info", "enc", "data", "battle", "evt", "event"):
            obj = kwargs.get(k)
            if obj is not None:
                return obj
        return args[0] if args else None

    def _map_key_from_enc(self, enc) -> Optional[str]:
        m = getattr(enc, "map", None)
        if m is None:
            return None
        name = getattr(m, "name", None)
        return str(name) if isinstance(name, str) and name else str(m)

    def _normalized_mode_from_enc(self, enc) -> Optional[str]:
        t = getattr(enc, "type", None)
        raw = getattr(t, "name", None)
        if isinstance(raw, str) and raw:
            n = raw.upper()
            if n in {"GRASS", "WALKING", "LAND"}:
                return "GRASS"
            if n in {"SURFING", "SURF", "WATER"}:
                return "SURF"
            if n in {"OLD_ROD", "GOOD_ROD", "SUPER_ROD", "FISHING", "FISH"}:
                return "SURF" if GROUP_FISHING_WITH_WATER else "ROD"
            if n in {"ROCK_SMASH", "ROCKSMASH", "SMASH"}:
                return "ROCK_SMASH"
            if n in {"STARTER", "GIFT", "STATIC", "GIFTPOKEMON", "GIFT_POKEMON", "EVENT"}:
                return "STATIC"
            if n in {"SAFARI"}:
                return "SAFARI"

        try:
            bm = getattr(enc, "bot_mode", None)
            if isinstance(bm, str) and bm:
                bmu = bm.upper()
                if "SPIN" in bmu:
                    return "GRASS"
                if "FISH" in bmu or "ROD" in bmu:
                    return "ROD"
                if "SURF" in bmu or "WATER" in bmu:
                    return "SURF"
        except Exception:
            pass

        try:
            mode_obj = getattr(context, "mode", None)
            mode_name = (getattr(mode_obj, "name", "") or "").upper()
            if "SPIN" in mode_name:
                return "GRASS"
            if "FISH" in mode_name or "ROD" in mode_name:
                return "ROD"
            if "SURF" in mode_name or "WATER" in mode_name:
                return "SURF"
            if "ROCK" in mode_name and "SMASH" in mode_name:
                return "ROCK_SMASH"
        except Exception:
            pass
        return (self.current_mode or "GRASS")

    def _species_from_enc(self, enc) -> Optional[str]:
        name = getattr(getattr(enc, "pokemon", None), "species_name", None)
        if not name:
            name = getattr(enc, "pokemon_name", None)
        if not name:
            poke = getattr(enc, "pokemon", None)
            sp = getattr(poke, "species", None)
            name = getattr(sp, "name", None) if sp is not None else None
        if isinstance(name, str) and name:
            return name.upper()
        for attr in ("current_encounter", "encounter", "battle", "last_encounter"):
            obj = getattr(context, attr, None)
            sp = getattr(getattr(obj, "pokemon", None), "species", None) if obj else None
            nm = getattr(sp, "name", None) if sp is not None else None
            if isinstance(nm, str) and nm:
                return nm.upper()
        return None

    # --------------------------------------------------
    # Species lookup (robust / case-insensitive)
    # --------------------------------------------------
    def _lookup_species_by_name(self, name: str):
        n = (name or "").strip()
        if not n:
            return None
        if _get_species_by_name:
            for candidate in (n, n.title(), n.capitalize(), n.lower()):
                try:
                    sp = _get_species_by_name(candidate)
                    if sp is not None:
                        return sp
                except Exception:
                    pass
        for attr in ("dex_by_name", "species_by_name", "pokemon_by_name", "dex"):
            d = getattr(context, attr, None)
            try:
                if isinstance(d, dict):
                    return d.get(n) or d.get(n.upper()) or d.get(n.lower()) or d.get(n.title())
            except Exception:
                pass
        for attr in ("dex_list", "species_list", "pokemon_list", "species", "all_species"):
            arr = getattr(context, attr, None)
            try:
                if isinstance(arr, (list, tuple)):
                    for sp in arr:
                        nm = getattr(sp, "name", None)
                        if isinstance(nm, str) and nm.lower() == n.lower():
                            return sp
            except Exception:
                pass
        return None

    def _lookup_species_by_index(self, idx: int):
        if not isinstance(idx, int):
            return None
        if _get_species_by_index:
            try:
                sp = _get_species_by_index(idx)
                if sp is not None:
                    return sp
            except Exception:
                pass
        for attr in ("species_names", "dex_names", "pokemon_names"):
            arr = getattr(context, attr, None)
            try:
                if arr and 0 <= idx < len(arr):
                    nm = arr[idx]
                    if isinstance(nm, str):
                        return self._lookup_species_by_name(nm)
            except Exception:
                pass
        for attr in ("dex_list", "species_list", "pokemon_list", "species", "all_species"):
            arr = getattr(context, attr, None)
            try:
                if isinstance(arr, (list, tuple)) and 0 <= idx < len(arr):
                    return arr[idx]
            except Exception:
                pass
        return None

    # --------------------------------------------------
    # Debug helpers
    # --------------------------------------------------
    def _debug_dump_enc(self, enc) -> None:
        try:
            if enc is None:
                _log_info("[ShinyQuota] EncounterInfo: <None>")
                return
            fields = []
            for a in ("pokemon", "type", "value", "map", "coordinates", "bot_mode"):
                v = getattr(enc, a, None)
                if hasattr(v, "name"):
                    try:
                        fields.append(f"{a}={getattr(v, 'name')}")
                        continue
                    except Exception:
                        pass
                fields.append(f"{a}={v}")
            _log_info("[ShinyQuota] EncounterInfo: " + ", ".join(fields))
        except Exception:
            pass
