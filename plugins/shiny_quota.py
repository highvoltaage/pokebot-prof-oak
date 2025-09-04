# -*- coding: utf-8 -*-
# plugins/shiny_quota.py
# "Learn-as-you-go" shiny quota for Emerald/FRLG.
# Keys hunts by EncounterInfo.map (enum) + normalized encounter MODE (GRASS/WATER/ROD/etc).
# OWNERSHIP SOURCE: live scan of PC storage + party (written to plugins/ProfOak/owned_shinies.json)

import json
import os
import time
from typing import Dict, List, Optional, Set, Iterable

from modules.plugin_interface import BotPlugin
from modules.context import context
from modules.runtime import get_base_path
from modules.pokemon import Pokemon

# Storage / Party helpers (fork-compatible imports)
try:
    from modules.pokemon_storage import get_pokemon_storage  # type: ignore
except Exception:
    get_pokemon_storage = None  # type: ignore

# Party lives in modules.pokemon_party (some forks export get_party, others get_pokemon_party)
try:
    from modules.pokemon_party import get_party as _get_party  # type: ignore
except Exception:
    try:
        from modules.pokemon_party import get_pokemon_party as _get_party  # type: ignore
    except Exception:
        _get_party = None  # type: ignore


PLUGIN_NAME = "ShinyQuota"

# ---------------- CONFIG ----------------
GLOBAL_SPECIES_OWNERSHIP = True   # Any shiny of a species counts globally (PC + party)
PAUSE_ACTION = "pause"            # "pause" or "manual"
DEBUG_DUMP = False                # Print EncounterInfo structure at battle start
GROUP_FISHING_WITH_WATER = False  # True => OLD/GOOD/SUPER ROD collapse into WATER instead of ROD

# ---------------- Paths (under plugins/ProfOak) ----------------
def _profoak_dir():
    return get_base_path() / "plugins" / "ProfOak"

PROFOAK_DIR = _profoak_dir()
LEARNED_PATH = PROFOAK_DIR / "emerald_learned_by_mapmode.json"  # encountered species per (map_enum, MODE)
OWNED_DB_PATH = PROFOAK_DIR / "owned_shinies.json"              # shiny species owned (PC + party)

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

# ---- emulator readiness helper (prevents FR/LG init crash) ----
def _emulator_ready() -> bool:
    """Return True once context.emulator exists and can answer get_frame_count()."""
    try:
        emu = getattr(context, "emulator", None)
        if emu is None:
            return False
        gc = getattr(emu, "get_frame_count", None)
        if callable(gc):
            _ = gc()  # raises until emulator is fully attached
            return True
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
    version = "0.2.2-alpha.0"
    description = "Pause when you have a shiny of every species you've encountered on this map+mode."
    author = "HighVoltaage"

    def __init__(self) -> None:
        # Ensure plugins/ProfOak exists
        try:
            PROFOAK_DIR.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass

        # Load learned encounters DB (map+mode -> species[])
        self.learned: Dict[str, Dict[str, List[str]]] = _read_json(LEARNED_PATH) or {}

        # Current hunt key (set on battle start)
        self.current_map_key: Optional[str] = None  # e.g., "RSE_ROUTE_118"
        self.current_mode: str = "GRASS"

        # Live caches
        self.required_species_current: Set[str] = set()
        self.owned_species_global: Set[str] = set()

        # Defer first PC/party scan until emulator is ready (fixes FR/LG init timing)
        self._pending_initial_scan = True
        _log_info(f"[{PLUGIN_NAME}] Initialized. Learned @ {LEARNED_PATH.name}, Owned @ {OWNED_DB_PATH.name} (scan deferred)")

    def get_additional_bot_modes(self) -> Iterable[type]:  # none added
        return ()

    def on_profile_loaded(self, *_args, **_kwargs) -> None:
        # Try to perform the initial scan now; if emulator not ready, defer to first battle.
        if _emulator_ready():
            self._refresh_owned_species_global(write_out=True)
            self._pending_initial_scan = False
        else:
            _log_warn(f"[{PLUGIN_NAME}] Emulator not ready; will scan PC/party on first battle.")

        _log_info(f"[{PLUGIN_NAME}] Profile loaded. Shinies known: {len(self.owned_species_global)}")

    # --------------------------------------------------
    # Battle hooks
    # --------------------------------------------------
    def on_battle_started(self, encounter=None, *args, **kwargs) -> None:
        """Learn species for this (map_enum, mode) using EncounterInfo."""
        try:
            # If we still owe the initial scan, try now (emulator is usually ready by first battle)
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

            self.required_species_current = set(per_map.get(mode_key, []))
            self._update_status()
            self._maybe_pause_if_quota_met()
        except Exception as e:
            _log_warn(f"on_battle_started error: {e}")

    def on_pokemon_caught(self, mon: Pokemon, *args, **kwargs) -> None:
        """After every catch, rescan PC+party and update owned DB, then re-check quota."""
        try:
            if _emulator_ready():
                self._refresh_owned_species_global(write_out=True)
            self._maybe_pause_if_quota_met()
        except Exception as e:
            _log_warn(f"on_pokemon_caught error: {e}")

    # --------------------------------------------------
    # Core
    # --------------------------------------------------
    def _maybe_pause_if_quota_met(self) -> None:
        if not self.current_map_key or not self.required_species_current:
            return

        owned = self.owned_species_global if GLOBAL_SPECIES_OWNERSHIP else set()
        missing = sorted(self.required_species_current - owned)
        self._update_status()

        if self.required_species_current and not missing:
            msg = f"✅ Quota met on {self.current_map_key} ({self.current_mode})."
            _notify(msg)
            try:
                if PAUSE_ACTION == "pause" and hasattr(context, "pause") and callable(getattr(context, "pause")):
                    context.pause()
                elif PAUSE_ACTION == "manual" and hasattr(context, "set_manual") and callable(getattr(context, "set_manual")):
                    context.set_manual(True)
            except Exception as e:
                _log_warn(f"Could not change bot state: {e}")
        else:
            if missing:
                _log_info(f"[{PLUGIN_NAME}] Missing {len(missing)} on {self.current_map_key}: {', '.join(missing[:5])}…")

    def _update_status(self) -> None:
        try:
            have = len(self.required_species_current & self.owned_species_global)
            total = len(self.required_species_current)
            _status(f"[{PLUGIN_NAME}] {have}/{total} shinies — {self.current_map_key or 'UNKNOWN'} {self.current_mode}")
        except Exception:
            pass

    # --------------------------------------------------
    # OWNERSHIP: PC storage + party
    # --------------------------------------------------
    def _refresh_owned_species_global(self, write_out: bool = False) -> None:
        """Scan PC storage and party to build the set of shiny species; optionally write DB JSON."""
        if not _emulator_ready():
            _log_warn(f"[{PLUGIN_NAME}] Emulator not ready; skipping PC/party scan for now.")
            return

        owned: Set[str] = set()

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
                            if isinstance(nm, str) and nm:
                                owned.add(nm.upper())
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
                it = getattr(mons, "pokemon", mons)  # list or container with .pokemon
                for mon in it:
                    if not mon:
                        continue
                    if getattr(mon, "is_shiny", False):
                        nm = getattr(mon, "species_name", None) \
                             or getattr(getattr(mon, "species", None), "name", None)
                        if isinstance(nm, str) and nm:
                            owned.add(nm.upper())
        except Exception as e:
            _log_warn(f"[{PLUGIN_NAME}] Party scan failed: {e}")

        self.owned_species_global = owned

        if write_out:
            try:
                data = {
                    "last_scan_epoch": int(time.time()),
                    "species": sorted(owned),
                }
                _write_json(OWNED_DB_PATH, data)
                _log_info(f"[{PLUGIN_NAME}] Shinies in PC+party: {len(owned)} species (DB updated)")
            except Exception as e:
                _log_warn(f"[{PLUGIN_NAME}] Could not write owned shinies DB: {e}")
        else:
            _log_info(f"[{PLUGIN_NAME}] Shinies in PC+party: {len(owned)} species")

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
        """
        Prefer EncounterInfo.type (enum) and normalize to tidy buckets:
        GRASS, WATER, ROD, ROCK_SMASH, STATIC, SAFARI, etc.
        Falls back to context.mode.name or enc.bot_mode if unavailable.
        """
        t = getattr(enc, "type", None)
        raw = getattr(t, "name", None)
        if isinstance(raw, str) and raw:
            n = raw.upper()

            # fishing detection
            if n in ("OLD_ROD", "GOOD_ROD", "SUPER_ROD", "FISHING", "ROD"):
                return "WATER" if GROUP_FISHING_WITH_WATER else "ROD"

            # water traversal
            if n in ("SURFING", "UNDERWATER", "WATER"):
                return "WATER"

            # land-like encounters (grass/cave/buildings/etc.)
            if n in ("GRASS", "LAND", "WALKING", "CAVE", "BUILDING", "DESERT", "INSIDE"):
                return "GRASS"

            # smashing rocks
            if n in ("ROCK_SMASH", "SMASH"):
                return "ROCK_SMASH"

            # safari zone
            if n in ("SAFARI", "SAFARI_ZONE"):
                return "SAFARI"

            # static / gifts / events
            if n in ("STATIC", "GIFT", "EVENT", "LEGENDARY"):
                return "STATIC"

            # unknown -> keep the enum name so you can see it in JSON
            _log_info(f"[{PLUGIN_NAME}] Encounter type '{n}' (keeping as-is).")
            return n

        # Fallbacks if EncounterInfo.type missing
        try:
            md = getattr(context, "mode", None)
            nm = getattr(md, "name", None)
            if nm:
                return str(nm).upper()
        except Exception:
            pass

        bm = getattr(enc, "bot_mode", None)
        if isinstance(bm, str) and bm:
            return bm.upper()

        return "GRASS"  # safe default

    def _species_from_enc(self, enc) -> Optional[str]:
        p = getattr(enc, "pokemon", None)
        if p is None:
            return None
        s = getattr(p, "species_name", None)
        if isinstance(s, str) and s:
            return s.upper()
        n = getattr(p, "name", None)
        return n.upper() if isinstance(n, str) and n else None

    # --------------------------------------------------
    # Optional: one-shot structure dump to help debugging
    # --------------------------------------------------
    def _debug_dump_enc(self, enc) -> None:
        try:
            if enc is None:
                _log_info(f"[{PLUGIN_NAME}] EncounterInfo: None")
                return
            lines = []
            def add(label, val):
                try:
                    lines.append(f"{label}: {repr(val)[:140]}")
                except Exception:
                    lines.append(f"{label}: <unreprable>")
            add("type(enc)", f"{type(enc).__module__}.{type(enc).__name__}")
            add("map", getattr(enc, "map", None))
            add("map.name", getattr(getattr(enc, "map", None), "name", None))
            add("type(enum)", getattr(getattr(enc, "type", None), "name", None))
            add("bot_mode", getattr(enc, "bot_mode", None))
            p = getattr(enc, "pokemon", None)
            add("pokemon.species_name", getattr(p, "species_name", None))
            _log_info(f"[{PLUGIN_NAME}] EncounterInfo dump →\n  " + "\n  ".join(lines))
        except Exception:
            pass
