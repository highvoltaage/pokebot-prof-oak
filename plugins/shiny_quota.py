# -*- coding: utf-8 -*-
# plugins/ProfOak/shiny_quota.py
from __future__ import annotations

import json, os, time, glob
from pathlib import Path
from typing import Dict, List, Optional, Set, Iterable, Tuple, Generator  # keep Generator visible

from modules.plugin_interface import BotPlugin
from modules.context import context
from modules.runtime import get_base_path
from modules.pokemon import Pokemon

# Optional helpers (forks vary)
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
PAUSE_ACTION = "navigate"           # "pause", "manual", or "navigate"
DEBUG_DUMP = False
LIVING_DEBUG = False
GROUP_FISHING_WITH_WATER = False

AUTO_BLOCK_COMPLETED_SPECIES = False
CATCH_BLOCK_PATH = get_base_path() / "profiles" / "catch_block.yml"

def _profoak_dir():
    return get_base_path() / "plugins" / "ProfOak"


def _resolve_json_dir(base: Path) -> Path:
    for name in ("JSON", "json"):
        candidate = base / name
        if candidate.exists():
            return candidate
    return base / "JSON"


PROFOAK_DIR    = _profoak_dir()
JSON_DIR       = _resolve_json_dir(PROFOAK_DIR)
LEARNED_PATH   = PROFOAK_DIR / "emerald_learned_by_mapmode.json"
OWNED_DB_PATH  = PROFOAK_DIR / "owned_shinies.json"
UNOWN_SEEN_PATH = PROFOAK_DIR / "unown_letters_seen.json"
ROM_SPECIES_PATH = PROFOAK_DIR / "rom_species_by_map.json"  # debug/reference
ROUTE_ORDER_PATH = JSON_DIR / "emerald_route_order.json"

def _find_encounters_json() -> Optional[str]:
    # Prefer a file that matches ROM id; else, first encounters_*.json we see.
    try:
        rom_id = getattr(getattr(context, "rom", None), "id", None)
    except Exception:
        rom_id = None
    preferred = None
    if isinstance(rom_id, str) and rom_id:
        cand = JSON_DIR / f"encounters_{rom_id}.json"
        if cand.exists():
            preferred = str(cand)
    if preferred:
        return preferred
    for p in sorted(glob.glob(str(JSON_DIR / "encounters_*.json"))):
        return p
    return None

ENCOUNTERS_JSON_PATH = _find_encounters_json()

# ---------------- logging/status ----------------
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

# ---------------- JSON I/O ----------------
def _read_json(path) -> dict:
    try:
        if path and hasattr(path, "exists") and path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
        if isinstance(path, str) and os.path.exists(path):
            return json.loads(open(path, "r", encoding="utf-8").read())
    except Exception: pass
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
    version = "0.5.0"
    description = "Pause when current route+method shiny quota is met. Families count (evolutions included)."
    author = "HighVoltaage"

    SUPPORTS_LIVING_DEX = True  # ProfOak may toggle

    def __init__(self) -> None:
        try: PROFOAK_DIR.mkdir(parents=True, exist_ok=True)
        except Exception: pass
        try: JSON_DIR.mkdir(parents=True, exist_ok=True)
        except Exception: pass

        self.learned: Dict[str, Dict[str, List[str]]] = _read_json(LEARNED_PATH) or {}
        self.unown_seen_by_map: Dict[str, List[str]] = _read_json(UNOWN_SEEN_PATH) or {}
        self.rom_species_by_map: Dict[str, Dict[str, List[str]]] = _read_json(ROM_SPECIES_PATH) or {}

        # New: preloaded encounter index + route order
        self._encounters_index: Dict[Tuple[int,int], Dict[str, List[str]]] = {}
        self._route_order: List[dict] = []
        self._load_encounter_index()
        _log_info(f"[{PLUGIN_NAME}] Encounters index loaded: {len(self._encounters_index)} maps.")
        self._load_route_order()

        self.current_map_key: Optional[str] = None
        self.current_mode: str = "GRASS"
        self._current_group_number: Optional[Tuple[int,int]] = None  # (group, number) if we can read it

        self.livingdex_enabled: bool = False
        self._explicit_living: Optional[bool] = None

        self.owned_species_global: Set[str] = set()
        self.owned_counts_global: Dict[str, int] = {}

        # key -> {"rep": str, "need": int, "members": set[str]}
        self.required_families_current: Dict[str, Dict[str, object]] = {}

        self._pending_initial_scan = True
        self._navigate_deferred = False  # remember to navigate after battle

        _log_info(f"[{PLUGIN_NAME}] Initialized. Learned @ {LEARNED_PATH.name}, Owned @ {OWNED_DB_PATH.name} (scan deferred)")
        if ENCOUNTERS_JSON_PATH:
            _log_info(f"[{PLUGIN_NAME}] Using encounter index: {os.path.basename(ENCOUNTERS_JSON_PATH)}")
        else:
            _log_warn(f"[{PLUGIN_NAME}] No encounters_*.json found in {JSON_DIR}")

    # ---------------- external toggles ----------------
    def set_livingdex_enabled(self, enabled: bool) -> None:
        self._explicit_living = bool(enabled)
        self.livingdex_enabled = self._explicit_living
        _log_info(f"[{PLUGIN_NAME}] Living Dex {'ENABLED' if self.livingdex_enabled else 'DISABLED'}.")
        self._rebuild_requirements_cache(); self._update_status()

    def clear_livingdex_override(self) -> None:
        self._explicit_living = None

    def set_quota_mode(self, mode: str) -> None:
        m = (mode or '').strip().upper()
        if m == "LIVING": self.set_livingdex_enabled(True)
        elif m == "STANDARD": self.set_livingdex_enabled(False)

    def _sync_livingdex_from_mode(self) -> None:
        if self._explicit_living is not None: return
        try:
            mode_obj = getattr(context, "mode", None)
            hint_attr = getattr(mode_obj, "is_living", None)
            if isinstance(hint_attr, bool):
                should = hint_attr
            else:
                name = (getattr(mode_obj, "name", "") or "").upper()
                should = any(tok in name for tok in ("LIVING","LDEX","L-DEX","LIVINGDEX"))
            if should != self.livingdex_enabled:
                self.livingdex_enabled = should
                _log_info(f"[{PLUGIN_NAME}] Mode sync → livingdex_enabled={self.livingdex_enabled}")
                self._rebuild_requirements_cache(); self._update_status()
        except Exception: pass

    def force_refresh(self) -> None:
        try:
            if _emulator_ready(): self._refresh_owned_species_global(write_out=False)
        except Exception: pass
        self._rebuild_requirements_cache(); self._update_status()

    # ---- BotPlugin interface ----
    def get_additional_bot_modes(self) -> Iterable[type]: return ()

    def on_profile_loaded(self, *_args, **_kwargs) -> None:
        if _emulator_ready():
            self._refresh_owned_species_global(write_out=True)
            self._pending_initial_scan = False
        else:
            _log_warn(f"[{PLUGIN_NAME}] Emulator not ready; will scan PC/party on first battle.")
        self._sync_livingdex_from_mode()
        _log_info(f"[{PLUGIN_NAME}] Profile loaded. Shinies known: {len(self.owned_species_global)} species")

    def on_mode_changed(self, *args, **kwargs) -> None:
        self._sync_livingdex_from_mode()
        self._rebuild_requirements_cache(); self._update_status()
        self._maybe_pause_if_quota_met()

    # -------- battle lifecycle --------
    def on_battle_started(self, encounter=None, *args, **kwargs) -> Generator | None:
        try:
            if getattr(self, "_pending_initial_scan", False) and _emulator_ready():
                self._refresh_owned_species_global(write_out=True)
                self._pending_initial_scan = False

            self._sync_livingdex_from_mode()

            enc = encounter or self._get_encounterinfo(args, kwargs)
            if DEBUG_DUMP: self._debug_dump_enc(enc)
            if enc is None:
                _log_warn(f"[{PLUGIN_NAME}] No EncounterInfo payload; cannot learn.")
                return None

            map_key = self._map_key_from_enc(enc)
            mode_key = self._normalized_mode_from_enc(enc)
            if not map_key or not mode_key:
                _log_warn(f"[{PLUGIN_NAME}] Missing map/mode on EncounterInfo; skipping learn.")
                return None
            self.current_map_key, self.current_mode = map_key, mode_key
            self._current_group_number = self._extract_group_number_from_enc(enc)

            species = self._species_from_enc(enc)  # may return UNOWN-X
            if not species:
                _log_warn(f"[{PLUGIN_NAME}] Could not read species at battle start.")
                return None

            # record Unown letter (per-map), if present
            if species.startswith("UNOWN-"):
                letters = set(self.unown_seen_by_map.get(self.current_map_key, []))
                letters.add(species.split("-", 1)[1])
                self.unown_seen_by_map[self.current_map_key] = sorted(letters)
                _write_json(UNOWN_SEEN_PATH, self.unown_seen_by_map)

            per_map = self.learned.setdefault(map_key, {})
            cur = set(per_map.get(mode_key, []))
            # prevent generic UNOWN if lettered form exists
            if species.startswith("UNOWN-"):
                cur = {s for s in cur if s != "UNOWN"}
            if species not in cur:
                cur.add(species); per_map[mode_key] = sorted(cur)
                _write_json(LEARNED_PATH, self.learned)
                _log_info(f"[{PLUGIN_NAME}] Learned {species} on {map_key} ({mode_key}).")

            self._rebuild_requirements_cache(); self._update_status()

            # IMPORTANT: do not navigate during battle; just mark for after-battle
            self._maybe_pause_if_quota_met(in_battle=True)
            return None
        except Exception as e:
            _log_warn(f"on_battle_started error: {e}")
            return None

    def on_pokemon_caught(self, mon: Pokemon, *args, **kwargs) -> Generator | None:
        try:
            if not getattr(mon, "is_shiny", False): return None
            sp_base = (getattr(mon, "species_name", "") or "").upper()
            if not sp_base: return None
            # Normalize UNOWN to lettered, if possible:
            letter = getattr(mon, "unown_letter", None)
            sp = f"UNOWN-{str(letter).upper()}" if sp_base == "UNOWN" and isinstance(letter, str) and letter else sp_base
            self._register_shiny_in_caches(sp)
            self._write_owned_db_snapshot()
            self._update_catch_block_if_needed()
            self._rebuild_requirements_cache(); self._update_status()

            # Also defer navigation here (still in battle)
            self._maybe_pause_if_quota_met(in_battle=True)
            return None
        except Exception as e:
            _log_warn(f"on_pokemon_caught error: {e}")
            return None

    # After-battle hooks (different forks use different names)
    def _run_deferred_navigation(self) -> Generator | None:
        if not self._navigate_deferred:
            return None
        self._navigate_deferred = False
        if PAUSE_ACTION == "navigate":
            _notify(f"✅ Quota met on {self.current_map_key} ({self.current_mode}). Navigating to next route…")
            return self._invoke_navigator()
        return None

    def on_battle_ended(self, *args, **kwargs) -> Generator | None:
        return self._run_deferred_navigation()

    def on_wild_encounter_finished(self, *args, **kwargs) -> Generator | None:
        return self._run_deferred_navigation()

    def on_battle_over(self, *args, **kwargs) -> Generator | None:
        return self._run_deferred_navigation()
        self._refresh_owned_species_global

    # --------------------------------------------------
    # Family requirements
    # --------------------------------------------------
    def _rebuild_requirements_cache(self) -> None:
        """Build required family entries for **current map + method**, preferring JSON encounter index."""
        self.required_families_current.clear()
        if not self.current_map_key: return

        # 1) learned species for this map/method
        per_mode = self.learned.get(self.current_map_key, {})
        learned_species = list(per_mode.get(self.current_mode, []))

        # 2) Prefer JSON encounter index for current map/method
        json_species = self._json_species_for_current_method() or []

        # 3) Fallback to legacy ROM peek only if JSON missing
        if not json_species:
            rom_species = self._rom_species_for_current_method() or []
        else:
            rom_species = []

        # Merge (+ expand Unown letters if we’ve seen them here)
        species_list = self._merge_species_with_unown_letters(learned_species + json_species, rom_species)

        if not species_list:
            return

        groups: Dict[str, Dict[str, object]] = {}
        for sname in species_list:
            s_up = sname.upper()
            member_names = self._family_species_names_from_name(s_up)  # full family, or singleton for UNOWN-*
            need = len(member_names) if self.livingdex_enabled else 1
            groups[s_up] = {"rep": s_up, "members": member_names, "need": need}
            if LIVING_DEBUG:
                _log_info(f"[{PLUGIN_NAME}] Family rep {s_up}: need={need}, members={sorted(member_names)}")
        self.required_families_current = groups

    def _merge_species_with_unown_letters(self, learned: List[str], rom_list: List[str]) -> List[str]:
        """Combine sources, de-dup, and expand generic UNOWN with letters we’ve seen on this map."""
        items = set()

        def add(name: str):
            n = (name or "").upper().strip()
            if not n: return
            items.add(n)

        for s in learned: add(s)
        for s in rom_list: add(s)

        # If we’ve seen letters here, expand:
        if "UNOWN" in items:
            letters = self.unown_seen_by_map.get(self.current_map_key or "", [])
            if letters:
                items.discard("UNOWN")
                for L in letters:
                    add(f"UNOWN-{str(L).upper()}")

        # If we also have lettered forms, ensure generic UNOWN is not present:
        has_lettered = any(x.startswith("UNOWN-") for x in items)
        if has_lettered and "UNOWN" in items:
            items.discard("UNOWN")

        return sorted(items)

    def _family_species_names_from_name(self, species_name: str) -> Set[str]:
        """
        Build the full evolution family names for species_name:

        Special case: UNOWN-<LETTER> => singleton family.
        """
        if species_name.startswith("UNOWN-"):
            return {species_name}

        names: Set[str] = set()
        sp = self._lookup_species_by_name(species_name)
        if sp is None:
            return {species_name.upper()}

        # Prefer direct family field
        fam = getattr(sp, "family", None)
        if isinstance(fam, list) and fam:
            for idx in fam:
                s2 = self._lookup_species_by_index(int(idx))
                nm = getattr(s2, "name", None) if s2 is not None else None
                if isinstance(nm, str) and nm:
                    names.add(nm.upper())
            if names:
                return names

        # Fallback: climb to root then BFS
        root = sp
        safety = 0
        while safety < 50:
            prev_idx = getattr(root, "evolves_from", None)
            if not isinstance(prev_idx, int): break
            cand = self._lookup_species_by_index(prev_idx)
            if cand is None: break
            root = cand; safety += 1

        stack = [root]; visited = set()
        while stack:
            node = stack.pop()
            if node in visited: continue
            visited.add(node)
            nm = getattr(node, "name", None)
            if isinstance(nm, str) and nm: names.add(nm.upper())
            evos = getattr(node, "evolutions", None) or []
            for evo in evos:
                tgt = None
                if isinstance(evo, dict):
                    for k in ("into","species","target","to","index"):
                        v = evo.get(k); 
                        if isinstance(v, int): tgt = v; break
                else:
                    for k in ("into","species","target","to","index"):
                        v = getattr(evo, k, None)
                        if isinstance(v, int): tgt = v; break
                if isinstance(tgt, int):
                    child = self._lookup_species_by_index(tgt)
                    if child is not None: stack.append(child)

        if not names: names.add(species_name.upper())
        return names

    def _covered_for_entry(self, entry: dict) -> int:
        """
        How much of this family is covered by owned shinies?

        - LivingDex: count *copies* across the whole family (e.g., 5x WURMPLE counts as 5).
        - Standard: any one member present counts as 1, otherwise 0.
        """
        members: set[str] = entry["members"]  # type: ignore
        if self.livingdex_enabled:
            return sum(self.owned_counts_global.get(n, 0) for n in members)
        else:
            total = sum(self.owned_counts_global.get(n, 0) for n in members)
            return 1 if total > 0 else 0

    # --- navigator glue: only returns a Generator when configured for navigation
    def _invoke_navigator(self) -> Optional[Generator]:
        try:
            from importlib import import_module
            nav_mod = import_module("plugins.ProfOak.navigator")
            nav_fn = getattr(nav_mod, "navigate_after_quota", None)
            if not callable(nav_fn):
                _log_warn(f"[{PLUGIN_NAME}] Navigator entrypoint not found.")
                return None
            current_map = self._current_group_number if self._current_group_number else self.current_map_key
            gen = nav_fn(
                context=context,
                current_map=current_map,
                method=self.current_mode,
                learned=self.learned,
                owned_counts=self.owned_counts_global,
            )
            return gen  # pass generator to engine
        except Exception as e:
            _log_warn(f"[{PLUGIN_NAME}] Navigator error: {e}")
            return None

    def _maybe_pause_if_quota_met(self, *, in_battle: bool = False) -> Generator | None:
        if not self.required_families_current: 
            return None
        deficits = []
        for entry in self.required_families_current.values():
            need = int(entry["need"]); covered = self._covered_for_entry(entry)
            if covered < need: deficits.append((str(entry["rep"]), need - covered))
        self._update_catch_block_if_needed(); self._update_status()

        if not deficits:
            if PAUSE_ACTION == "navigate":
                if in_battle:
                    # Defer navigator until the battle has ended
                    if not self._navigate_deferred:
                        _log_info(f"[{PLUGIN_NAME}] Quota met; deferring navigation until battle ends.")
                    self._navigate_deferred = True
                    return None
                # Not in battle: return generator now
                _notify(f"✅ Quota met on {self.current_map_key} ({self.current_mode}). Navigating to next route…")
                gen = self._invoke_navigator()
                if gen is not None:
                    return gen
            # Fallback to old behavior (pause/manual)
            self._hit_quota()
            return None
        else:
            top = ", ".join(f"{s}×{d}" for s, d in deficits[:5])
            label = "LivingDex" if self.livingdex_enabled else "Standard"
            _log_info(f"[{PLUGIN_NAME}] {label} missing ({len(deficits)}): {top}…")

            # NEW: also show backlog (current + previous routes) filtered by abilities
            try:
                backlog = self._compute_backlog_deficits()
                for method, pairs in backlog.items():
                    if not pairs:
                        continue
                    msg = ", ".join(f"{s}×{d}" for s, d in pairs[:6])
                    _log_info(f"[{PLUGIN_NAME}] {method} backlog: {msg}…")
            except Exception as e:
                _log_warn(f"[{PLUGIN_NAME}] backlog compute failed: {e}")

            return None

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
            total_need = sum(int(e["need"]) for e in self.required_families_current.values())
            total_have = 0
            for e in self.required_families_current.values():
                total_have += min(int(e["need"]), self._covered_for_entry(e))
            label = "Living" if self.livingdex_enabled else "Std"
            _status(f"[{PLUGIN_NAME}] {total_have}/{total_need} ({label}) — {self.current_map_key or 'UNKNOWN'} {self.current_mode}")
        except Exception: pass

    # --------------------------------------------------
    # OWNERSHIP: PC storage + party (counts)
    # --------------------------------------------------
    def _refresh_owned_species_global(self, write_out: bool = False) -> None:
        if not _emulator_ready():
            _log_warn(f"[{PLUGIN_NAME}] Emulator not ready; skipping PC/party scan for now.")
            return
        owned_set: Set[str] = set()
        owned_counts: Dict[str, int] = {}

        def _bump(name: Optional[str], mon_obj=None):
            if not isinstance(name, str) or not name: return
            n = name.upper()
            # Normalize UNOWN to lettered, if we can:
            if n == "UNOWN" and mon_obj is not None:
                L = getattr(mon_obj, "unown_letter", None)
                if isinstance(L, str) and L:
                    n = f"UNOWN-{L.upper()}"
            owned_set.add(n); owned_counts[n] = owned_counts.get(n, 0) + 1

        # PC
        try:
            if get_pokemon_storage:
                storage = get_pokemon_storage()
                for box in storage.boxes:
                    for slot in box.slots:
                        mon = slot.pokemon
                        if getattr(mon, "is_shiny", False):
                            nm = getattr(mon, "species_name", None) or getattr(getattr(mon, "species", None), "name", None)
                            _bump(nm, mon)
        except Exception as e:
            _log_warn(f"[{PLUGIN_NAME}] PC scan failed: {e}")

        # Party
        try:
            mons = _get_party() if _get_party else getattr(context, "party", None)
            it = getattr(mons, "pokemon", mons)
            if it:
                for mon in it:
                    if mon and getattr(mon, "is_shiny", False):
                        nm = getattr(mon, "species_name", None) or getattr(getattr(mon, "species", None), "name", None)
                        _bump(nm, mon)
        except Exception as e:
            _log_warn(f"[{PLUGIN_NAME}] Party scan failed: {e}")

        self.owned_species_global = owned_set
        self.owned_counts_global = owned_counts
        if write_out: self._write_owned_db_snapshot()
        else: _log_info(f"[{PLUGIN_NAME}] Shinies in PC+party: {sum(owned_counts.values())} mons, {len(owned_set)} species")

    def _write_owned_db_snapshot(self) -> None:
        try:
            data = {"last_scan_epoch": int(time.time()), "species_counts": dict(sorted(self.owned_counts_global.items()))}
            _write_json(OWNED_DB_PATH, data)
        except Exception as e:
            _log_warn(f"[{PLUGIN_NAME}] Could not write owned shinies DB: {e}")

    def _register_shiny_in_caches(self, species_upper: str) -> None:
        try:
            self.owned_species_global.add(species_upper)
            self.owned_counts_global[species_upper] = self.owned_counts_global.get(species_upper, 0) + 1
        except Exception: pass
        try: self._update_status()
        except Exception: pass

    # ---------------- catch_block.yml helpers ----------------
    def _pretty_species_name(self, s: str) -> str:
        try:
            sp = self._lookup_species_by_name(s); nm = getattr(sp, "name", None)
            if isinstance(nm, str) and nm: return nm
        except Exception: pass
        return s.title()

    def _read_catch_block(self) -> list[str]:
        items: list[str] = []
        try:
            text = CATCH_BLOCK_PATH.read_text(encoding="utf-8")
            in_block = False
            for line in text.splitlines():
                t = line.rstrip()
                if t.strip().startswith("#"): continue
                if t.strip().startswith("block_list:"):
                    in_block = True; continue
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
        done: set[str] = set()
        for entry in self.required_families_current.values():
            need = int(entry["need"]); covered = self._covered_for_entry(entry)
            if covered >= need and need > 0: done.add(str(entry["rep"]))
        return done

    def _update_catch_block_if_needed(self) -> None:
        if not AUTO_BLOCK_COMPLETED_SPECIES: return
        completed = self._completed_species_for_current_mode()
        if not completed: return
        current_items = self._read_catch_block()
        current_upper = {x.upper() for x in current_items}
        to_add = [self._pretty_species_name(s) for s in sorted(completed) if s.upper() not in current_upper]
        if not to_add: return
        new_items = current_items + to_add
        self._write_catch_block(new_items)
        _log_info(f"[{PLUGIN_NAME}] Added to catch_block.yml: {', '.join(to_add)}")

    # --------------------------------------------------
    # Encounter helpers (EncounterInfo & map/group/number)
    # --------------------------------------------------
    def _get_encounterinfo(self, args, kwargs):
        for k in ("encounter","info","enc","data","battle","evt","event"):
            obj = kwargs.get(k)
            if obj is not None: return obj
        return args[0] if args else None

    def _extract_group_number_from_enc(self, enc) -> Optional[Tuple[int,int]]:
        """Try to read (map_group, map_number) from the EncounterInfo.map object."""
        m = getattr(enc, "map", None)
        if m is None: return None
        for g_attr in ("map_group", "group", "map_group_number", "group_id"):
            g = getattr(m, g_attr, None)
            if isinstance(g, int):
                for n_attr in ("map_number", "number", "map_id", "index"):
                    n = getattr(m, n_attr, None)
                    if isinstance(n, int):
                        return (g, n)
        return None

    def _map_key_from_enc(self, enc) -> Optional[str]:
        m = getattr(enc, "map", None)
        if m is None: return None
        name = getattr(m, "name", None)
        return str(name) if isinstance(name, str) and name else str(m)

    def _normalized_mode_from_enc(self, enc) -> Optional[str]:
        t = getattr(enc, "type", None)
        raw = getattr(t, "name", None)
        if isinstance(raw, str) and raw:
            n = raw.upper()
            if n in {"GRASS","WALKING","LAND"}: return "GRASS"
            if n in {"SURFING","SURF","WATER"}: return "SURF"
            if n in {"OLD_ROD","GOOD_ROD","SUPER_ROD","FISHING","FISH"}:
                return "SURF" if GROUP_FISHING_WITH_WATER else "ROD"
            if n in {"ROCK_SMASH","ROCKSMASH","SMASH"}: return "ROCK_SMASH"
            if n in {"STARTER","GIFT","STATIC","GIFTPOKEMON","GIFT_POKEMON","EVENT"}: return "STATIC"
            if n in {"SAFARI"}: return "SAFARI"
        try:
            bm = getattr(enc, "bot_mode", None)
            if isinstance(bm, str) and bm:
                bmu = bm.upper()
                if "SPIN" in bmu: return "GRASS"
                if "FISH" in bmu or "ROD" in bmu: return "ROD"
                if "SURF" in bmu or "WATER" in bmu: return "SURF"
        except Exception: pass
        try:
            mode_obj = getattr(context, "mode", None)
            mode_name = (getattr(mode_obj, "name", "") or "").upper()
            if "SPIN" in mode_name: return "GRASS"
            if "FISH" in mode_name or "ROD" in mode_name: return "ROD"
            if "SURF" in mode_name or "WATER" in mode_name: return "SURF"
            if "ROCK" in mode_name and "SMASH" in mode_name: return "ROCK_SMASH"
        except Exception: pass
        return (self.current_mode or "GRASS")

    def _species_from_enc(self, enc) -> Optional[str]:
        name = getattr(getattr(enc, "pokemon", None), "species_name", None)
        if not name: name = getattr(enc, "pokemon_name", None)
        poke = getattr(enc, "pokemon", None)
        if not name and poke is not None:
            sp = getattr(poke, "species", None); name = getattr(sp, "name", None) if sp is not None else None
        if isinstance(name, str) and name:
            up = name.upper()
            if up == "UNOWN":
                L = getattr(poke, "unown_letter", None)
                if isinstance(L, str) and L:
                    return f"UNOWN-{L.upper()}"
            return up
        for attr in ("current_encounter","encounter","battle","last_encounter"):
            obj = getattr(context, attr, None)
            sp = getattr(getattr(obj, "pokemon", None), "species", None) if obj else None
            nm = getattr(sp, "name", None) if sp is not None else None
            if isinstance(nm, str) and nm:
                return nm.upper()
        return None

    # --------------------------------------------------
    # Species lookup (robust)
    # --------------------------------------------------
    def _lookup_species_by_name(self, name: str):
        n = (name or "").strip()
        if not n: return None
        if _get_species_by_name:
            for candidate in (n, n.title(), n.capitalize(), n.lower()):
                try:
                    sp = _get_species_by_name(candidate)
                    if sp is not None: return sp
                except Exception: pass
        for attr in ("dex_by_name","species_by_name","pokemon_by_name","dex"):
            d = getattr(context, attr, None)
            try:
                if isinstance(d, dict):
                    return d.get(n) or d.get(n.upper()) or d.get(n.lower()) or d.get(n.title())
            except Exception: pass
        for attr in ("dex_list","species_list","pokemon_list","species","all_species"):
            arr = getattr(context, attr, None)
            try:
                if isinstance(arr, (list, tuple)):
                    for sp in arr:
                        nm = getattr(sp, "name", None)
                        if isinstance(nm, str) and nm.lower() == n.lower(): return sp
            except Exception: pass
        return None

    def _lookup_species_by_index(self, idx: int):
        if not isinstance(idx, int): return None
        # 1) API function
        if _get_species_by_index:
            try:
                sp = _get_species_by_index(idx)
                if sp is not None: return sp
            except Exception: pass
        # 2) Try list access (0-based then 1-based fallback)
        for attr in ("dex_list","species_list","pokemon_list","species","all_species"):
            arr = getattr(context, attr, None)
            try:
                if isinstance(arr, (list, tuple)):
                    if 0 <= idx < len(arr): return arr[idx]
                    if 1 <= idx <= len(arr): return arr[idx-1]
            except Exception: pass
        # 3) Name array then by-name
        for attr in ("species_names","dex_names","pokemon_names"):
            arr = getattr(context, attr, None)
            try:
                if arr:
                    if 0 <= idx < len(arr): nm = arr[idx]
                    elif 1 <= idx <= len(arr): nm = arr[idx-1]
                    else: nm = None
                    if isinstance(nm, str): return self._lookup_species_by_name(nm)
            except Exception: pass
        return None

    # --------------------------------------------------
    # ROM/JSON encounter integration
    # --------------------------------------------------
    def _load_encounter_index(self) -> None:
        """Create a fast index: {(g,n): {GRASS:[...], SURF:[...], ROD:[...], ROCK_SMASH:[...]}}"""
        self._encounters_index.clear()
        try:
            data = _read_json(ENCOUNTERS_JSON_PATH) if ENCOUNTERS_JSON_PATH else {}
            # Accept either {"maps":[...]} or a plain list/dict
            iterable = None
            if isinstance(data, dict) and "maps" in data and isinstance(data["maps"], list):
                iterable = data["maps"]
            elif isinstance(data, list):
                iterable = data
            elif isinstance(data, dict):
                # maybe keyed by "g:n"
                iterable = []
                for k, v in data.items():
                    v2 = dict(v); v2["_key"] = k
                    iterable.append(v2)
            if not iterable:
                return

            def norm_method_key(k: str) -> Optional[str]:
                ku = (k or "").upper()
                if ku in {"GRASS","LAND"}: return "GRASS"
                if ku in {"SURF"}: return "SURF"
                if ku in {"ROCK_SMASH","ROCKSMASH"}: return "ROCK_SMASH"
                if ku in {"FISHING","ROD","OLD_ROD","GOOD_ROD","SUPER_ROD"}: return "ROD"
                return None

            for entry in iterable:
                if not isinstance(entry, dict): 
                    continue
                g = entry.get("map_group") or entry.get("group") or entry.get("g")
                n = entry.get("map_number") or entry.get("number") or entry.get("n")
                if not (isinstance(g, int) and isinstance(n, int)):
                    # try parse "g:n"
                    key = entry.get("_key")
                    if isinstance(key, str) and ":" in key:
                        gs, ns = key.split(":", 1)
                        try: g = int(gs); n = int(ns)
                        except Exception: 
                            continue
                    else:
                        continue
                pools = {}
                # methods container might be under "methods" or flattened
                methods_obj = entry.get("methods", entry)
                # collect sets
                rod_collect: Set[str] = set()
                for mk, mv in list(methods_obj.items()):
                    norm = norm_method_key(mk)
                    if not norm: 
                        continue
                    names: Set[str] = set()
                    if isinstance(mv, list):
                        for nm in mv:
                            if isinstance(nm, str) and nm:
                                names.add(nm.upper())
                    elif isinstance(mv, dict):
                        for vv in mv.values():
                            if isinstance(vv, list):
                                for nm in vv:
                                    if isinstance(nm, str) and nm:
                                        names.add(nm.upper())
                    if norm == "ROD":
                        rod_collect |= names
                    else:
                        pools.setdefault(norm, set()).update(names)
                if rod_collect:
                    pools.setdefault("ROD", set()).update(rod_collect)
                # finalize to lists
                final = {k: sorted(list(v)) for k, v in pools.items()}
                self._encounters_index[(g, n)] = final
        except Exception as e:
            _log_warn(f"[{PLUGIN_NAME}] Failed to load encounter index: {e}")
            
    def _bootstrap_current_map(self) -> None:
        """Try to set self._current_group_number and self.current_map_key outside battle,
        so the JSON encounter index can be used immediately."""
        try:
            import importlib
            m = importlib.import_module("modules.map")

            # Try the most explicit helper first
            for fn_name in (
                "get_current_map_group_and_number",   # (context) -> (g, n)
                "get_current_map_group_number",       # () -> (g, n)
            ):
                fn = getattr(m, fn_name, None)
                if callable(fn):
                    try:
                        try:
                            g, n = fn(context)  # variant taking context
                        except TypeError:
                            g, n = fn()         # variant without context
                        if isinstance(g, int) and isinstance(n, int):
                            self._current_group_number = (g, n)
                            break
                    except Exception:
                        pass

            # Try object-returning API (map header / info object)
            if not self._current_group_number:
                for fn_name in ("get_current_map", "get_overworld_map"):
                    fn = getattr(m, fn_name, None)
                    if callable(fn):
                        try:
                            mp = fn(context) if fn.__code__.co_argcount else fn()
                            g = getattr(mp, "map_group", None)
                            n = getattr(mp, "map_number", None)
                            if isinstance(g, int) and isinstance(n, int):
                                self._current_group_number = (g, n)
                                # Name if available:
                                nm = getattr(mp, "name", None)
                                if isinstance(nm, str) and nm:
                                    self.current_map_key = nm
                        except Exception:
                            pass

            # If we still don't have a displayable name, try route_order lookup
            if self._current_group_number and not self.current_map_key and self._route_order:
                g, n = self._current_group_number
                for ent in self._route_order:
                    eg = ent.get("map_group") or ent.get("group")
                    en = ent.get("map_number") or ent.get("number")
                    if eg == g and en == n:
                        nm = ent.get("name")
                        if isinstance(nm, str) and nm:
                            self.current_map_key = nm
                        break

            # Last-ditch fallback label
            if self._current_group_number and not self.current_map_key:
                g, n = self._current_group_number
                self.current_map_key = f"MAP_{g}_{n}"
        except Exception as e:
            _log_warn(f"[{PLUGIN_NAME}] _bootstrap_current_map failed: {e}")


    def _json_species_for_map_method(self, g: int, n: int, method: str) -> List[str] | None:
        pools = self._encounters_index.get((g, n))
        if not pools:
            return None
        m = (method or "GRASS").upper()
        if m == "GRASS":
            for k in ("GRASS", "LAND"):
                if k in pools: return list(pools[k])
        if m == "SURF":
            if "SURF" in pools: return list(pools["SURF"])
        if m == "ROCK_SMASH":
            for k in ("ROCK_SMASH","ROCKSMASH"):
                if k in pools: return list(pools[k])
        if m == "ROD":
            if "ROD" in pools: return list(pools["ROD"])
        return None

    def _json_species_for_current_method(self) -> List[str] | None:
        if not self._current_group_number:
            return None
        g, n = self._current_group_number
        return self._json_species_for_map_method(g, n, self.current_mode)

    def _rom_species_for_current_method(self) -> List[str] | None:
        """Legacy ROM peek; kept as a fallback if JSON is unavailable."""
        try:
            if not self._current_group_number: return None
            import importlib
            m = importlib.import_module("modules.map")
            if not hasattr(m, "get_wild_encounters_for_map"): return None
            g, n = self._current_group_number
            enc_list = m.get_wild_encounters_for_map(g, n)  # returns a WildEncounterList-like object
            if not enc_list: return None

            def pick_pool():
                method = self.current_mode
                if method == "GRASS":
                    for k in ("grass","land"): 
                        lst = getattr(enc_list, k, None)
                        if lst is not None: return lst
                if method == "SURF":
                    for k in ("surf",): 
                        lst = getattr(enc_list, k, None)
                        if lst is not None: return lst
                if method == "ROD":
                    for k in ("fishing","old_rod_encounters","good_rod_encounters","super_rod_encounters"):
                        lst = getattr(enc_list, k, None)
                        if lst: return lst
                if method == "ROCK_SMASH":
                    for k in ("rock_smash",): 
                        lst = getattr(enc_list, k, None)
                        if lst is not None: return lst
                return None

            pool = pick_pool()
            if pool is None:
                return None

            names: Set[str] = set()
            for e in pool:
                sp = getattr(e, "species", None)
                nm = getattr(sp, "name", None) if sp is not None else None
                if isinstance(nm, str) and nm:
                    names.add(nm.upper())

            # Write debug snapshot for reference
            if self.current_map_key:
                per_map = self.rom_species_by_map.setdefault(self.current_map_key, {})
                per_map[self.current_mode] = sorted(names)
                _write_json(ROM_SPECIES_PATH, self.rom_species_by_map)

            return sorted(names)
        except Exception:
            return None

    # --------------------------------------------------
    # Route order + capability-filtered backlog
    # --------------------------------------------------
    def _load_route_order(self) -> None:
        self._route_order = []
        try:
            data = _read_json(ROUTE_ORDER_PATH)
            if isinstance(data, list):
                self._route_order = data
            elif isinstance(data, dict) and "order" in data and isinstance(data["order"], list):
                self._route_order = data["order"]
        except Exception as e:
            _log_warn(f"[{PLUGIN_NAME}] Could not load route order: {e}")

    def _get_current_capabilities(self) -> Set[str]:
        """Ask plugins.ProfOak.capabilities for current capability set; default to GRASS."""
        try:
            from plugins.ProfOak.capabilities import get_current_capabilities  # type: ignore
            caps = get_current_capabilities(context)
            if isinstance(caps, (set, list, tuple)):
                return {str(x).upper() for x in caps}
        except Exception:
            pass
        return {"GRASS"}

    def _compute_backlog_deficits(self) -> Dict[str, List[Tuple[str,int]]]:
        """Aggregate missing across route_order from start → current map (inclusive), grouped by method,
        filtered by capabilities."""
        out: Dict[str, Dict[str,int]] = {"GRASS":{}, "SURF":{}, "ROD":{}, "ROCK_SMASH":{}}
        caps = self._get_current_capabilities()
        if not self._route_order or not self._current_group_number:
            return {k:[] for k in out}
        g_now, n_now = self._current_group_number

        # find current index
        idx = 0
        found = False
        for i, ent in enumerate(self._route_order):
            g = ent.get("map_group") or ent.get("group")
            n = ent.get("map_number") or ent.get("number")
            if g == g_now and n == n_now:
                idx = i
                found = True
                break
        if not found:
            idx = len(self._route_order) - 1

        # iterate from start to idx
        for i in range(0, idx + 1):
            ent = self._route_order[i]
            g = ent.get("map_group") or ent.get("group")
            n = ent.get("map_number") or ent.get("number")
            if not (isinstance(g, int) and isinstance(n, int)): 
                continue

            for method in ("GRASS","SURF","ROD","ROCK_SMASH"):
                if method not in caps:
                    continue
                sp_list = self._json_species_for_map_method(g, n, method) or []
                # Expand possible UNOWN via letters seen on that map (if available there)
                expand = set(sp_list)
                if "UNOWN" in expand:
                    letters = self.unown_seen_by_map.get(self.current_map_key or "", [])
                    if letters:
                        expand.discard("UNOWN")
                        for L in letters: expand.add(f"UNOWN-{str(L).upper()}")

                for rep in sorted(expand):
                    fam = self._family_species_names_from_name(rep)
                    need = len(fam) if self.livingdex_enabled else 1
                    have = sum(self.owned_counts_global.get(nm, 0) for nm in fam)
                    deficit = max(0, need - have)
                    if deficit > 0:
                        out[method][rep] = max(out[method].get(rep, 0), deficit)

        # convert to sorted lists
        return {m: sorted(list(d.items()), key=lambda x: (-x[1], x[0])) for m, d in out.items()}

    # --------------------------------------------------
    # Debug
    # --------------------------------------------------
    def _debug_dump_enc(self, enc) -> None:
        try:
            if enc is None:
                _log_info("[ShinyQuota] EncounterInfo: <None>"); return
            fields = []
            for a in ("pokemon","type","value","map","coordinates","bot_mode"):
                v = getattr(enc, a, None)
                if hasattr(v, "name"):
                    try: fields.append(f"{a}={getattr(v,'name')}"); continue
                    except Exception: pass
                fields.append(f"{a}={v}")
            _log_info("[ShinyQuota] EncounterInfo: " + ", ".join(fields))
        except Exception: pass
