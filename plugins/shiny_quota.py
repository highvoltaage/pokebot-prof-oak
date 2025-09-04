# -*- coding: utf-8 -*-
# plugins/shiny_quota.py
# Minimal "learn-as-you-go" shiny quota for Emerald.
# Keys hunts by EncounterInfo.map (enum) + a normalized encounter MODE (GRASS/WATER/ROD/etc).

import json
import os
from typing import Dict, List, Optional, Set, Iterable

from modules.plugin_interface import BotPlugin
from modules.context import context
from modules.runtime import get_base_path
from modules.pokemon import Pokemon

PLUGIN_NAME = "ShinyQuota"

# ---------------- CONFIG ----------------
GLOBAL_SPECIES_OWNERSHIP = True             # Any shiny of a species counts globally
PAUSE_ACTION = "pause"                      # "pause" or "manual"
DEBUG_DUMP = False                          # Print EncounterInfo structure at battle start
GROUP_FISHING_WITH_WATER = False            # True => OLD/GOOD/SUPER ROD are normalized to WATER instead of ROD

# Persisted file: species we have LEARNED for each (map_enum, mode)
LEARNED_PATH = get_base_path() / "data" / "emerald_learned_by_mapmode.json"

# ----------------- tiny logging helpers -----------------
def _log_info(msg: str) -> None:
    for attr in ("logger", "log"):
        lg = getattr(context, attr, None)
        if lg and hasattr(lg, "info"):
            try: lg.info(msg); return
            except Exception: pass
    print(msg)

def _log_warn(msg: str) -> None:
    for attr in ("logger", "log"):
        lg = getattr(context, attr, None)
        if lg and hasattr(lg, "warning"):
            try: lg.warning(msg); return
            except Exception: pass
        if lg and hasattr(lg, "warn"):
            try: lg.warn(msg); return
            except Exception: pass
    print(f"WARNING: {msg}")

def _notify(msg: str) -> None:
    try:
        if hasattr(context, "notify") and callable(getattr(context, "notify")):
            context.notify(msg); return
    except Exception: pass
    _log_info(f"[{PLUGIN_NAME}] {msg}")

def _status(msg: str) -> None:
    try:
        if hasattr(context, "overlay") and hasattr(context.overlay, "set_status_line"):
            context.overlay.set_status_line(msg)
    except Exception: pass

# ----------------- small JSON I/O -----------------
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
    version = "3.1.0"
    description = "Pause when you have a shiny of every species you've encountered on this map+mode."
    author = "you"

    def __init__(self) -> None:
        self.learned: Dict[str, Dict[str, List[str]]] = _read_json(LEARNED_PATH) or {}
        self.current_map_key: Optional[str] = None  # e.g., "RSE_ROUTE_101"
        self.current_mode: str = "GRASS"

        self.required_species_current: Set[str] = set()
        self.owned_species_global: Set[str] = set()
        self._refresh_owned_species_global()

        _log_info(f"[{PLUGIN_NAME}] Initialized. Learned file: {LEARNED_PATH}")

    def get_additional_bot_modes(self) -> Iterable[type]:  # none added
        return ()

    def on_profile_loaded(self, *_args, **_kwargs) -> None:
        self._refresh_owned_species_global()
        _log_info(f"[{PLUGIN_NAME}] Profile loaded. Shinies known: {len(self.owned_species_global)}")

    # --------------------------------------------------
    # Battle hooks
    # --------------------------------------------------
    def on_battle_started(self, *args, **kwargs) -> None:
        """Learn species for this (map_enum, mode) using EncounterInfo."""
        try:
            enc = self._get_encounterinfo(args, kwargs)
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
        try:
            if not getattr(mon, "is_shiny", False):
                return
            species = (getattr(mon, "species_name", "") or "").upper()
            if not species:
                return
            self.owned_species_global.add(species)
            _log_info(f"[{PLUGIN_NAME}] Shiny obtained: {species}")
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
        if m is None: return None
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

            # land-like encounters (grass, walking, cave interiors, buildings, desert)
            if n in ("GRASS", "LAND", "WALKING", "CAVE", "BUILDING", "DESERT", "INSIDE"):
                return "GRASS"

            # smashing rocks
            if n in ("ROCK_SMASH", "SMASH"):
                return "ROCK_SMASH"

            # safari zone
            if n in ("SAFARI", "SAFARI_ZONE"):
                return "SAFARI"

            # static / gift / event mons
            if n in ("STATIC", "GIFT", "EVENT", "LEGENDARY"):
                return "STATIC"

            # unknown -> keep the enum name so you can see it in JSON
            _log_info(f"[{PLUGIN_NAME}] Encounter type '{n}' (keeping as-is).")
            return n

        # Fallbacks if EncounterInfo.type missing
        try:
            md = getattr(context, "mode", None)
            nm = getattr(md, "name", None)
            if nm: return str(nm).upper()
        except Exception:
            pass

        bm = getattr(enc, "bot_mode", None)
        if isinstance(bm, str) and bm:
            return bm.upper()

        return "GRASS"  # safe default

    def _species_from_enc(self, enc) -> Optional[str]:
        p = getattr(enc, "pokemon", None)
        if p is None: return None
        s = getattr(p, "species_name", None)
        if isinstance(s, str) and s:
            return s.upper()
        n = getattr(p, "name", None)
        return n.upper() if isinstance(n, str) and n else None

    # --------------------------------------------------
    # Owned shinies (seed once from output folder)
    # --------------------------------------------------
    def _refresh_owned_species_global(self) -> None:
        owned: Set[str] = set()
        try:
            shiny_dir = get_base_path() / "output" / "shinies"
            if shiny_dir.is_dir():
                for fname in os.listdir(shiny_dir):
                    base = os.path.splitext(fname)[0]
                    parts = base.split("-")
                    for p in reversed(parts):
                        if p.isalpha():
                            owned.add(p.upper()); break
        except Exception:
            pass
        self.owned_species_global = owned

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
                try: lines.append(f"{label}: {repr(val)[:140]}")
                except Exception: lines.append(f"{label}: <unreprable>")
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
