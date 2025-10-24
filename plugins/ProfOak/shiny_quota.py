# -*- coding: utf-8 -*-
"""
plugins/ProfOak/shiny_quota.py

Shiny Quota (Prof Oak) with:
- ROM encounter integration (authoritative even when empty)
- Learned JSON seeding/pruning from ROM summary (all methods)
- Living-Dex support (family-based counts + Unown A..Z)
- Robust debug prints to verify ROM reads and selection source
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set, Tuple

from modules.plugin_interface import BotPlugin
from modules.context import context
from modules.runtime import get_base_path
from modules.pokemon import Pokemon, Species
from modules.battle_state import BattleOutcome

PLUGIN_NAME = "ShinyQuota"

# =============================================================================
#                                   CONFIG
# =============================================================================

# Where plugin assets live
PLUGIN_DIR: Path = Path(__file__).resolve().parent
JSON_DIR: Path = PLUGIN_DIR / "JSON"
JSON_DIR.mkdir(parents=True, exist_ok=True)

# Files
REGISTRY_PATH  = JSON_DIR / "shiny_registry.json"      # (optional) per-route provenance
LEARNED_PATH   = JSON_DIR / "learned_by_mapmode.json"  # encountered/species per map+method
OWNED_SNAPSHOT = JSON_DIR / "owned_shinies.json"       # last PC+party scan
WILD_DATA_PATH = JSON_DIR / "wild_by_mapmode.json"     # optional static tables

# Behavior
GLOBAL_SPECIES_OWNERSHIP = True
USE_LEARNED_SPECIES_AS_REQUIREMENTS = True
PREFER_ROM_WHEN_AVAILABLE = True
PRUNE_LEARNED_WITH_ROM = True           # drop stale learned entries for methods ROM says are empty
LIVINGDEX_DEFAULT = False
ON_QUOTA = "TEST"                       # MANUAL / NAVIGATOR / TEST

# Debug
DEBUG_DUMP = True

# Unown configuration
UNOWN_FORMS: List[str] = [chr(c) for c in range(ord('A'), ord('Z') + 1)]
UNOWN_LETTERS_PATH: Path = JSON_DIR / "unown_letters_seen.json"

# =============================================================================
#                              Small utilities
# =============================================================================
def _log_info(msg: str) -> None:
    lg = getattr(context, "logger", None) or getattr(context, "log", None)
    if lg and hasattr(lg, "info"):
        try: lg.info(msg); return
        except Exception: pass
    print(msg)

def _log_warn(msg: str) -> None:
    lg = getattr(context, "logger", None) or getattr(context, "log", None)
    for m in ("warning", "warn"):
        if lg and hasattr(lg, m):
            try: getattr(lg, m)(msg); return
            except Exception: pass
    print(f"WARNING: {msg}")

def _notify(msg: str) -> None:
    try:
        n = getattr(context, "notify", None)
        if callable(n): n(msg); return
    except Exception: pass
    _log_info(f"[{PLUGIN_NAME}] {msg}")

def _set_status_line(msg: str) -> None:
    try:
        if hasattr(context, "overlay") and hasattr(context.overlay, "set_status_line"):
            context.overlay.set_status_line(msg)
    except Exception: pass

def _emulator_ready() -> bool:
    try:
        emu = getattr(context, "emulator", None)
        return emu is not None and callable(getattr(emu, "get_frame_count", None))
    except Exception:
        return False

def _normalize_species(name: Optional[str]) -> Optional[str]:
    return name.strip().upper() if isinstance(name, str) and name.strip() else None

def _normalize_method(m: Optional[str]) -> str:
    if not m: return "GRASS"
    n = str(m).strip().upper()
    if n in ("GRASS", "LAND", "WALKING", "OVERWORLD"): return "GRASS"
    if n in ("SURF", "WATER"): return "SURF"
    if n in ("ROCKSMASH", "ROCK_SMASH", "SMASH"): return "ROCK_SMASH"
    if n in ("FISH", "FISHING", "ROD", "OLD_ROD", "GOOD_ROD", "SUPER_ROD"): return "ROD"
    # Modes like "Spin" should default to GRASS
    return "GRASS"

def _read_json(path: Path) -> dict:
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception: pass
    return {}

def _write_json(path: Path, data: dict) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    except Exception as e:
        _log_warn(f"[{PLUGIN_NAME}] Failed to write {path}: {e}")

# ======================================================================================
# Map helpers
# ======================================================================================

def _get_current_map_group_number() -> Optional[Tuple[int, int]]:
    try:
        pa = getattr(context, "player_avatar", None)
        mgn = getattr(pa, "map_group_and_number", None)
        if isinstance(mgn, tuple) and len(mgn) == 2:
            return int(mgn[0]), int(mgn[1])
    except Exception:
        pass

    for a, b in (("map_group", "map_number"), ("current_map_group", "current_map_number")):
        try:
            g = getattr(context, a, None)
            n = getattr(context, b, None)
            if isinstance(g, int) and isinstance(n, int):
                return g, n
        except Exception:
            pass
    return None

# ======================================================================================
# ROM encounter integration + debug
# ======================================================================================

def _species_from_rom_for_current(method: str) -> Optional[Set[str]]:
    """Return species set for *current method* from ROM. None means ROM couldn’t be read."""
    import traceback as _tb
    def _dbg(m: str): 
        if DEBUG_DUMP: _log_info(f"[{PLUGIN_NAME}][ROM] {m}")

    try:
        import modules.map as mapmod
        _dbg(f"Imported modules.map = {mapmod!r}")
    except Exception as e:
        _dbg(f"import modules.map failed: {e}")
        _dbg(_tb.format_exc()); return None

    eff_fn = getattr(mapmod, "get_effective_encounter_rates_for_current_map", None)
    _dbg(f"has get_effective_encounter_rates_for_current_map: {callable(eff_fn)}")
    if not callable(eff_fn): return None

    try:
        eff = eff_fn()
        _dbg(f"effective() returned: {type(eff).__name__}")
        if eff is None: 
            _dbg("effective() is None"); return None
    except Exception as e:
        _dbg(f"effective() raised: {e}")
        _dbg(_tb.format_exc()); return None

    we = getattr(eff, "regular_encounters", None)
    _dbg(f"regular_encounters present: {we is not None} ({type(we).__name__ if we is not None else 'None'})")
    if we is None: return None

    def names(enc_list) -> Set[str]:
        s: Set[str] = set()
        for e in enc_list or []:
            sp = getattr(e, "species", None)
            nm = getattr(sp, "name", None) if sp is not None else None
            nm = _normalize_species(nm)
            if nm: s.add(nm)
        return s

    m = _normalize_method(method)
    _dbg(f"normalized method = {m}")

    try:
        if m == "GRASS":      return names(getattr(we, "land_encounters", []))
        if m == "SURF":       return names(getattr(we, "surf_encounters", []))
        if m == "ROCK_SMASH": return names(getattr(we, "rock_smash_encounters", []))
        if m == "ROD":
            a = names(getattr(we, "old_rod_encounters", []))
            b = names(getattr(we, "good_rod_encounters", []))
            c = names(getattr(we, "super_rod_encounters", []))
            return a | b | c
    except Exception as e:
        _dbg(f"while mapping method tables: {e}")
        _dbg(_tb.format_exc())
        return None
    return set()

def _rom_table_summary_for_current() -> Optional[Dict[str, List[str]]]:
    """Return {METHOD: [species]} for all buckets on current map, or None if ROM not readable."""
    import traceback as _tb
    def _dbg(m: str): 
        if DEBUG_DUMP: _log_info(f"[{PLUGIN_NAME}][ROM] {m}")

    try:
        import modules.map as mapmod
    except Exception as e:
        _dbg(f"import modules.map failed: {e}")
        _dbg(_tb.format_exc()); return None

    eff_fn = getattr(mapmod, "get_effective_encounter_rates_for_current_map", None)
    if not callable(eff_fn): return None
    try:
        eff = eff_fn()
        if eff is None: return None
    except Exception:
        return None

    we = getattr(eff, "regular_encounters", None)
    if we is None: return None

    def to_list(enc_list) -> List[str]:
        s: Set[str] = set()
        for e in enc_list or []:
            sp = getattr(e, "species", None)
            nm = getattr(sp, "name", None) if sp is not None else None
            nm = _normalize_species(nm)
            if nm: s.add(nm)
        return sorted(s)

    grass = to_list(getattr(we, "land_encounters", []))
    surf  = to_list(getattr(we, "surf_encounters", []))
    rock  = to_list(getattr(we, "rock_smash_encounters", []))
    rod   = sorted(set().union(
        to_list(getattr(we, "old_rod_encounters", [])),
        to_list(getattr(we, "good_rod_encounters", [])),
        to_list(getattr(we, "super_rod_encounters", [])),
    ))
    if DEBUG_DUMP:
        _log_info(f"[{PLUGIN_NAME}][ROM] summary counts — GRASS={len(grass)}, SURF={len(surf)}, ROCK_SMASH={len(rock)}, ROD={len(rod)}")
    return {"GRASS": grass, "SURF": surf, "ROCK_SMASH": rock, "ROD": rod}

def _dump_rom_debug(tag: str = "") -> None:
    if not DEBUG_DUMP: return
    try:
        g_n = _get_current_map_group_number()
        _log_info(f"[{PLUGIN_NAME}][ROM][{tag}] map_group_number={g_n}")
    except Exception:
        pass
    summary = _rom_table_summary_for_current()
    if summary is None:
        _log_info(f"[{PLUGIN_NAME}][ROM][{tag}] summary=None")
        return
    def pv(lst: List[str]) -> str: return ", ".join(lst[:10]) + (" …" if len(lst) > 10 else "")
    _log_info(f"[{PLUGIN_NAME}][ROM][{tag}] GRASS: {len(summary['GRASS'])} [{pv(summary['GRASS'])}]")
    _log_info(f"[{PLUGIN_NAME}][ROM][{tag}] SURF: {len(summary['SURF'])} [{pv(summary['SURF'])}]")
    _log_info(f"[{PLUGIN_NAME}][ROM][{tag}] ROCK_SMASH: {len(summary['ROCK_SMASH'])} [{pv(summary['ROCK_SMASH'])}]")
    _log_info(f"[{PLUGIN_NAME}][ROM][{tag}] ROD: {len(summary['ROD'])} [{pv(summary['ROD'])}]")


# =============================================================================
#                                  Plugin
# =============================================================================
class ShinyQuotaPlugin(BotPlugin):
    name = PLUGIN_NAME
    version = "3.4.0"
    description = "Pauses (or navigates) when the shiny quota for the current map+method is complete."
    author = "you"

    def __init__(self) -> None:
        self.registry: Dict[str, Dict[str, List[int]]] = _read_json(REGISTRY_PATH) or {"default": {}}
        self.learned: Dict[str, Dict[str, List[str]]] = _read_json(LEARNED_PATH) or {}
        self.current_map_key: Optional[str] = None
        self.current_method: str = "GRASS"
        self.livingdex_enabled: bool = LIVINGDEX_DEFAULT
        self.owned_species_global: Set[str] = set()
        self.owned_counts_global: Dict[str, int] = {}
        self.required_species_route: Set[str] = set()
        self.required_families_current: Dict[int, Tuple[int, Set[str]]] = {}
        self.unown_letters_seen: Dict[str, Set[str]] = {}
        self._load_unown_letters_seen()
        _log_info(f"[{PLUGIN_NAME}] Initialized. Learned: {LEARNED_PATH.name}, Owned: {OWNED_SNAPSHOT.name}")

    # ---- hooks ---------------------------------------------------------------
    def get_additional_bot_modes(self) -> Iterable[type]: return ()

    def on_profile_loaded(self, *_a, **_k) -> None:
        self._refresh_current_map()
        self._refresh_method()
        if _emulator_ready():
            self._refresh_owned_species_global(write_out=True)
        else:
            _log_warn(f"[{PLUGIN_NAME}] Emulator not ready; will scan PC/party later.")
        if DEBUG_DUMP: _dump_rom_debug("on_profile_loaded")
        self._rebuild_route_requirements()
        self._rebuild_requirements_cache()
        self._print_missing_now()

    def on_mode_changed(self, *a, **k) -> None:
        self._refresh_method()
        if DEBUG_DUMP: _dump_rom_debug("on_mode_changed")
        self._rebuild_route_requirements()
        self._rebuild_requirements_cache()
        self._print_missing_now()

    def on_map_changed(self, *a, **k) -> None:
        self._refresh_current_map()
        if DEBUG_DUMP: _dump_rom_debug("on_map_changed")
        self._rebuild_route_requirements()
        self._rebuild_requirements_cache()
        self._print_missing_now()

    def _method_from_encounter(self, encounter) -> Optional[str]:
        """Best-effort extract of encounter method from EncounterInfo."""
        t = getattr(encounter, "type", None)
        if hasattr(t, "name"): t = getattr(t, "name")
        if isinstance(t, str):
            t = t.strip().upper()
            if "SURF" in t: return "SURF"
            if "FISH" in t or "ROD" in t: return "ROD"
            if "ROCK" in t and "SMASH" in t: return "ROCK_SMASH"
            if "LAND" in t or "GRASS" in t or "WALK" in t: return "GRASS"
        return None

    def on_logging_encounter(self, encounter) -> None:
        """Learn species and make sure map/method keys are fresh & correct."""
        try:
            mon = getattr(encounter, "pokemon", None)
            sp = getattr(mon, "species", None)
            nm = _normalize_species(getattr(sp, "name", None))
            if not nm: return

            # Map key from encounter.map if available
            map_obj = getattr(encounter, "map", None)
            if map_obj is not None:
                mk = _normalize_species(getattr(map_obj, "name", None) or getattr(map_obj, "value", None))
                if not mk and getattr(map_obj, "rep", None):
                    mk = _normalize_species(getattr(map_obj.rep, "name", None))
                if mk: self.current_map_key = mk

            # Method from encounter.type if present (prevents GRASS mis-buckets)
            m = self._method_from_encounter(encounter)
            if m: self.current_method = m
            else: self._refresh_method()

            letter = getattr(mon, "unown_letter", None) if nm == "UNOWN" else None

            self._commit_learn(nm, letter=letter)
            self._rebuild_route_requirements()
            self._rebuild_requirements_cache()
            self._print_missing_now()
        except Exception as e:
            _log_warn(f"[{PLUGIN_NAME}] on_logging_encounter failed: {e}")

    def on_pokemon_caught(self, mon: Pokemon, *a, **k) -> None:
        try:
            if not getattr(mon, "is_shiny", False): return
            self._refresh_current_map()
            self._refresh_method()

            raw_letter = getattr(mon, "unown_letter", None)
            nm = _normalize_species(getattr(mon, "species_name", None) or getattr(getattr(mon, "species", None), "name", None))

            if nm == "UNOWN" or (not nm and isinstance(raw_letter, str) and raw_letter.strip()):
                self._record_unown_letter(raw_letter)

            if nm:
                if nm == "UNOWN" and isinstance(raw_letter, str) and raw_letter.strip():
                    self._bump_owned(f"UNOWN-{raw_letter.strip().upper()}")
                self._bump_owned(nm)
                _write_json(OWNED_SNAPSHOT, {"species": sorted(self.owned_species_global), "counts": self.owned_counts_global})
            else:
                self._refresh_owned_species_global(write_out=True)

            self._rebuild_route_requirements()
            self._rebuild_requirements_cache()
            self._print_missing_now()
            self._maybe_quota_action()
        except Exception as e:
            _log_warn(f"[{PLUGIN_NAME}] on_pokemon_caught error: {e}")

    def on_battle_ended(self, outcome: BattleOutcome) -> None:
        self._refresh_current_map()
        self._refresh_method()

    # ---- core ----------------------------------------------------------------
    def _commit_learn(self, species: str, letter: Optional[str] = None) -> None:
        if not self.current_map_key or not species: return
        if species == "UNOWN":
            self._record_unown_letter(letter)
        per_map = self.learned.setdefault(self.current_map_key, {})
        cur = set(per_map.get(self.current_method, []))
        if species not in cur:
            cur.add(species)
            per_map[self.current_method] = sorted(cur)
            _write_json(LEARNED_PATH, self.learned)
            _log_info(f"[{PLUGIN_NAME}] Learned {species} on {self.current_map_key} ({self.current_method}).")

    def _load_unown_letters_seen(self) -> None:
        raw = _read_json(UNOWN_LETTERS_PATH)
        if not isinstance(raw, dict):
            return
        for map_key, letters in raw.items():
            if not isinstance(map_key, str):
                continue
            bucket: Set[str] = set()
            if isinstance(letters, list):
                for entry in letters:
                    if isinstance(entry, str) and entry.strip():
                        bucket.add(entry.strip().upper())
            if bucket:
                self.unown_letters_seen[map_key] = bucket

    def _persist_unown_letters_seen(self) -> None:
        serializable = {mk: sorted(letters) for mk, letters in self.unown_letters_seen.items() if letters}
        _write_json(UNOWN_LETTERS_PATH, serializable)

    def _record_unown_letter(self, letter: Optional[str]) -> bool:
        if not self.current_map_key:
            return False
        if not isinstance(letter, str) or not letter.strip():
            return False
        normalized = letter.strip().upper()
        letters = self.unown_letters_seen.setdefault(self.current_map_key, set())
        if normalized in letters:
            return False
        letters.add(normalized)
        self._persist_unown_letters_seen()
        return True

    def _unown_letters_for_current_map(self) -> Set[str]:
        letters: Set[str] = set()
        if self.current_map_key:
            letters.update(self.unown_letters_seen.get(self.current_map_key, set()))
            if not letters:
                per_map = self.learned.get(self.current_map_key, {})
                for lst in per_map.values():
                    for species in lst:
                        if isinstance(species, str) and species.startswith("UNOWN-"):
                            letters.add(species.split("-", 1)[1].strip().upper())
        if not letters:
            return set(UNOWN_FORMS)
        return {ltr.strip().upper() for ltr in letters if isinstance(ltr, str) and ltr.strip()}

    def _expand_unown_if_needed(self, species_set: Set[str]) -> Set[str]:
        if not self.livingdex_enabled: return species_set
        if "UNOWN" not in species_set: return species_set
        expanded = set(s for s in species_set if s != "UNOWN")
        for letter in sorted(self._unown_letters_for_current_map()):
            expanded.add(f"UNOWN-{letter}")
        return expanded

    def _merge_rom_into_learned(self, map_key: str) -> None:
        """ROM MERGE/PRUNE: seed learned JSON from ROM summary and prune stale buckets."""
        summary = _rom_table_summary_for_current()
        if summary is None: return
        per = self.learned.setdefault(map_key, {})
        changed = False

        # Merge
        for meth, lst in summary.items():
            ss = set(per.get(meth, []))
            before = len(ss)
            ss.update(lst)
            if len(ss) != before:
                per[meth] = sorted(ss); changed = True

        # Prune methods that have no ROM entries (avoid stale mis-learns)
        if PRUNE_LEARNED_WITH_ROM:
            for meth in ("GRASS", "SURF", "ROCK_SMASH", "ROD"):
                if meth not in summary or len(summary[meth]) == 0:
                    if per.get(meth):
                        per[meth] = []; changed = True

        if changed:
            _write_json(LEARNED_PATH, self.learned)
            _log_info(f"[{PLUGIN_NAME}] Learned JSON updated from ROM for {map_key}.")

    def _rebuild_route_requirements(self) -> None:
        self.required_species_route = set()
        if not self.current_map_key: self._refresh_current_map()
        if not self.current_method:  self._refresh_method()
        if not self.current_map_key: return

        map_key = self.current_map_key
        method  = self.current_method

        # Always try to sync learned with ROM when available
        self._merge_rom_into_learned(map_key)  # ROM MERGE/PRUNE

        learned_set: Set[str] = set()
        if USE_LEARNED_SPECIES_AS_REQUIREMENTS:
            learned_set = {s for s in (self.learned.get(map_key, {}).get(method, [])) if s}

        static_set: Set[str] = set()
        if WILD_DATA_PATH.exists():
            try:
                wild = json.loads(WILD_DATA_PATH.read_text(encoding="utf-8"))
                static_set = {s.upper() for s in wild.get(map_key, {}).get(method, [])}
            except Exception as e:
                _log_warn(f"[{PLUGIN_NAME}] wild-data read failed: {e}")

        # AUTHORITATIVE ROM (even when empty)
        rom_read = _species_from_rom_for_current(method)  # None = not readable; set() allowed
        selected: Set[str]; selected_src = "NONE"
        if PREFER_ROM_WHEN_AVAILABLE and rom_read is not None:
            selected, selected_src = rom_read, "ROM"
        elif static_set:
            selected, selected_src = static_set, "STATIC"
        elif learned_set:
            selected, selected_src = learned_set, "LEARNED"
        else:
            selected, selected_src = set(), "EMPTY"

        if DEBUG_DUMP:
            _log_info(f"[{PLUGIN_NAME}] requirements source = {selected_src} on {map_key} ({method})")
            _dump_rom_debug("rebuild_requirements")

        self.required_species_route = self._expand_unown_if_needed(selected)

        if selected_src == "EMPTY":
            _log_warn(f"No learned/spec data for {map_key} ({method}); quota will activate after first encounters.")

    def _rebuild_requirements_cache(self) -> None:
        self.required_families_current.clear()
        if not self.required_species_route: return

        fam_to_members: Dict[int, Set[str]] = {}
        for s in self.required_species_route:
            fam = self._family_root_index_for_species_name(s)
            fam_to_members.setdefault(fam, set()).add(s)

        for fam, members in fam_to_members.items():
            need = len(members) if self.livingdex_enabled else 1
            self.required_families_current[fam] = (need, members)

    # ---- ownership -----------------------------------------------------------
    def _bump_owned(self, name: Optional[str]) -> None:
        n = _normalize_species(name)
        if not n: return
        self.owned_species_global.add(n)
        self.owned_counts_global[n] = self.owned_counts_global.get(n, 0) + 1

    def _refresh_owned_species_global(self, write_out: bool = False) -> None:
        owned_set: Set[str] = set()
        owned_counts: Dict[str, int] = {}

        def bump(n: Optional[str]) -> None:
            nn = _normalize_species(n)
            if not nn: return
            owned_set.add(nn)
            owned_counts[nn] = owned_counts.get(nn, 0) + 1

        # PC
        try:
            from modules.pokemon_storage import get_pokemon_storage  # type: ignore
            storage = get_pokemon_storage()
            for box in storage.boxes:
                for slot in box.slots:
                    mon: Pokemon = slot.pokemon
                    if getattr(mon, "is_shiny", False):
                        nm = getattr(mon, "species_name", None) or getattr(getattr(mon, "species", None), "name", None)
                        bump(nm)
                        if _normalize_species(nm) == "UNOWN":
                            letter = getattr(mon, "unown_letter", None)
                            if isinstance(letter, str) and letter:
                                bump(f"UNOWN-{letter.upper()}")
        except Exception as e:
            _log_warn(f"[{PLUGIN_NAME}] PC scan failed: {e}")

        # Party
        try:
            from modules.pokemon_party import get_party  # type: ignore
            mons = get_party()
            for mon in getattr(mons, "pokemon", mons) or []:
                if mon and getattr(mon, "is_shiny", False):
                    nm = getattr(mon, "species_name", None) or getattr(getattr(mon, "species", None), "name", None)
                    bump(nm)
                    if _normalize_species(nm) == "UNOWN":
                        letter = getattr(mon, "unown_letter", None)
                        if isinstance(letter, str) and letter:
                            bump(f"UNOWN-{letter.upper()}")
        except Exception as e:
            _log_warn(f"[{PLUGIN_NAME}] Party scan failed: {e}")

        self.owned_species_global = owned_set
        self.owned_counts_global = owned_counts
        if write_out:
            _write_json(OWNED_SNAPSHOT, {"species": sorted(owned_set), "counts": owned_counts})
        _log_info(f"[{PLUGIN_NAME}] Shinies in PC+party: {sum(owned_counts.values())} mons, {len(owned_set)} species")

    # ---- families & mapping --------------------------------------------------
    def _family_root_index_for_species_name(self, species_name: str) -> int:
        if species_name.startswith("UNOWN-"):
            return self._family_root_index_for_species_name("UNOWN")
        try:
            dex = getattr(context, "species_index_by_name", None)
            idx = dex.get(species_name, None) if isinstance(dex, dict) else None
            if idx is None:
                member = getattr(Species, species_name.title(), None)
                idx = getattr(member, "index", None)
            if isinstance(idx, int):
                fam = getattr(context, "species_family_root", None)
                if isinstance(fam, list) and 0 <= idx < len(fam):
                    return int(fam[idx])
                return idx
        except Exception:
            pass
        return abs(hash(species_name)) % 1000003

    # ---- map/method refreshers ----------------------------------------------
    def _refresh_method(self) -> None:
        try:
            m = getattr(context, "mode", None)
            name = getattr(m, "name", None)
            if name:
                self.current_method = _normalize_method(str(name))
                return
        except Exception: pass
        self.current_method = "GRASS"

    def _refresh_current_map(self) -> None:
        for attr in ("current_map_name", "map_name", "region_name"):
            try:
                nm = getattr(context, attr, None)
                if isinstance(nm, str) and nm.strip():
                    self.current_map_key = _normalize_species(nm); return
            except Exception: pass
        try:
            for attr in ("read_region_map_section_id", "get_current_mapsec_id", "region_map_section_id"):
                fn = getattr(context, attr, None)
                val = int(fn()) if callable(fn) else int(fn) if fn is not None else None
                if isinstance(val, int):
                    self.current_map_key = f"MAPSEC_{val}"; return
        except Exception: pass
        g_n = _get_current_map_group_number()
        if g_n: self.current_map_key = f"MAP_{g_n[0]}_{g_n[1]}"

    # ---- status/printing/action ---------------------------------------------
    def _status_tuple(self) -> Tuple[int, int]:
        if self.livingdex_enabled and self.required_families_current:
            have = 0; total = 0
            for fam, (need, members) in self.required_families_current.items():
                total += need
                owned = sum(1 for s in members if s in self.owned_species_global)
                have += min(owned, need)
            return have, total
        total = len(self.required_species_route)
        have = len(self.required_species_route & self.owned_species_global)
        return have, total

    def _set_status_progress(self) -> None:
        have, total = self._status_tuple()
        _set_status_line(f"[{PLUGIN_NAME}] {have}/{total} route+mode shinies")

    def _missing_breakdown(self) -> List[str]:
        missing: List[str] = []
        if self.livingdex_enabled and self.required_families_current:
            for fam, (need, members) in self.required_families_current.items():
                have = sum(1 for s in members if s in self.owned_species_global)
                rem = max(0, need - have)
                if rem > 0:
                    todo = [s for s in sorted(members) if s not in self.owned_species_global][:3]
                    missing.append(f"{(todo[0] if todo else next(iter(members)))}×{rem}")
        else:
            for s in sorted(self.required_species_route):
                if s not in self.owned_species_global:
                    missing.append(f"{s}×1")
        return missing

    def _print_missing_now(self) -> None:
        have, total = self._status_tuple()
        self._set_status_progress()
        if total == 0: return
        if have >= total:
            _notify(f"✅ Quota met on {self.current_map_key} ({self.current_method}).")
        else:
            mlist = self._missing_breakdown()
            _log_info(f"[{PLUGIN_NAME}] Missing ({max(0, total - have)}): {',  '.join(mlist[:6])}…")

    def _maybe_quota_action(self) -> None:
        have, total = self._status_tuple()
        if total == 0 or have < total: return
        if ON_QUOTA == "TEST":
            _notify("✅ Quota met (TEST mode) — staying in current mode."); return
        if ON_QUOTA == "NAVIGATOR":
            _notify("✅ Quota met — handing off to Navigator (if available)."); return
        try:
            set_manual = getattr(context, "set_manual_mode", None) or getattr(context, "set_manual", None)
            if callable(set_manual): set_manual(); _notify("✅ Quota met — switched to Manual.")
        except Exception as e:
            _log_warn(f"[{PLUGIN_NAME}] Could not switch to Manual: {e}")
