# plugins/ProfOak/capabilities.py
# -----------------------------------------------------------------------------
# Prof Oak – Capability snapshot
#
# What this does
#  - Reads badges, key items (rods/bikes), and (optionally) whether your party
#    actually knows the HM moves gated by those badges.
#  - Exposes a simple dataclass `Capabilities` and two entry points:
#       compute_capabilities(require_party_move=True)  -> Capabilities
#       refresh_capabilities(require_party_move=True)  -> Capabilities (and caches)
#  - Designed to be robust across forks: all external calls are wrapped
#    and have fallbacks.
#
# Why this exists
#  - The navigator needs to know “can we Surf/Fly/… *right now*?”
#  - For HM traversal, many challenges (and your desired behavior) want
#    BOTH the badge *and* a party mon that knows the move; this file
#    supports that via `require_party_move=True` (default).
#
# Safe to import anywhere (plugins, modes, listeners).
# -----------------------------------------------------------------------------

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional, Set

# ---------- Optional debug toggle ----------
DEBUG_CAPS = False  # flip True for verbose one-line prints


# ---------- HM and item name tables (Gen III) ----------
HM_NAMES = {
    "cut": {"Cut"},
    "flash": {"Flash"},
    "rock_smash": {"Rock Smash"},
    "strength": {"Strength"},
    "surf": {"Surf"},
    "fly": {"Fly"},
    "dive": {"Dive"},
    "waterfall": {"Waterfall"},
}

# Key items we check in the bag
KEY_ITEMS = {
    "Old Rod": {"Old Rod"},
    "Good Rod": {"Good Rod"},
    "Super Rod": {"Super Rod"},
    "Mach Bike": {"Mach Bike"},
    "Acro Bike": {"Acro Bike"},
}

# Emerald badge event-flag IDs (from your emerald.txt list)
# If a fork exposes a direct badges API, we’ll prefer that and skip flags.
BADGE_FLAGS = {
    1: 2151,  # BADGE01_GET
    2: 2152,  # BADGE02_GET
    3: 2153,  # BADGE03_GET
    4: 2154,  # BADGE04_GET
    5: 2155,  # BADGE05_GET
    6: 2156,  # BADGE06_GET
    7: 2157,  # BADGE07_GET
    8: 2158,  # BADGE08_GET
}


# ---------- Data model ----------
@dataclass(frozen=True)
class Capabilities:
    badges: Set[int]

    has_old_rod: bool
    has_good_rod: bool
    has_super_rod: bool
    has_mach_bike: bool
    has_acro_bike: bool
    has_any_rod: bool

    can_cut: bool
    can_flash: bool
    can_rock_smash: bool
    can_strength: bool
    can_surf: bool
    can_fly: bool
    can_dive: bool
    can_waterfall: bool


# Module-level cache (optional)
_CAPS_CACHE: Optional[Capabilities] = None


# ---------- Small util: safe debug print ----------
def _dbg(msg: str) -> None:
    if DEBUG_CAPS:
        try:
            from modules.context import context
            lg = getattr(context, "logger", None) or getattr(context, "log", None)
            if lg and hasattr(lg, "info"):
                lg.info(msg)
                return
        except Exception:
            pass
        print(msg)


# ---------- Bag / save helpers (defensive across forks) ----------
def _get_save_data():
    try:
        from modules.save_data import get_save_data  # type: ignore
        return get_save_data()
    except Exception:
        return None


def _bag_has(item_name: str) -> bool:
    """
    Heuristic: check known places a fork may expose item counts.
    """
    # Try modern items table + bag object
    try:
        from modules.items import get_item_by_name  # type: ignore
        item = get_item_by_name(item_name)
        if item:
            sd = _get_save_data()
            if sd and hasattr(sd, "bag"):
                bag = sd.bag
                # common shapes: bag.get_item_count(idx) / bag.count(name)
                if hasattr(bag, "get_item_count"):
                    cnt = bag.get_item_count(getattr(item, "index", None))
                    return bool(cnt and cnt > 0)
                if hasattr(bag, "count"):
                    cnt = bag.count(item_name)
                    return bool(cnt and cnt > 0)
    except Exception:
        pass

    # Try context inventory
    try:
        from modules.context import context  # type: ignore
        inv = getattr(context, "inventory", None) or getattr(context, "bag", None)
        if inv:
            # common shapes: inv.get(name), inv.count(name), inv[name]
            if hasattr(inv, "get"):
                v = inv.get(item_name)
                if isinstance(v, int):
                    return v > 0
                if v is not None:
                    return True
            if hasattr(inv, "count"):
                return inv.count(item_name) > 0
            try:
                return bool(inv[item_name])
            except Exception:
                pass
    except Exception:
        pass

    return False


def _read_badges() -> Set[int]:
    """
    Prefer a direct badges API if available; otherwise read event flags
    using the Emerald IDs you provided. Returns {1..8} subset.
    """
    # Direct badges list/bitset on save
    sd = _get_save_data()
    if sd:
        # Common shapes:
        for attr in ("badges", "get_badges", "badge_flags"):
            obj = getattr(sd, attr, None)
            try:
                data = obj() if callable(obj) else obj
                if data:
                    # data might be list[bool] length 8 or an int bitmask
                    if isinstance(data, (list, tuple)) and len(data) >= 8:
                        return {i + 1 for i, v in enumerate(data[:8]) if bool(v)}
                    if isinstance(data, int):
                        return {i + 1 for i in range(8) if data & (1 << i)}
            except Exception:
                pass

        # Fallback: event flags by ID
        get_flag = getattr(sd, "get_event_flag", None)
        if callable(get_flag):
            res: Set[int] = set()
            for i, fid in BADGE_FLAGS.items():
                try:
                    if bool(get_flag(fid)):
                        res.add(i)
                except Exception:
                    pass
            if res:
                return res

    # Last resort: try context.player
    try:
        from modules.context import context  # type: ignore
        pl = getattr(context, "player", None)
        if pl:
            for attr in ("badges", "get_badges"):
                obj = getattr(pl, attr, None)
                data = obj() if callable(obj) else obj
                if data:
                    if isinstance(data, (list, tuple)) and len(data) >= 8:
                        return {i + 1 for i, v in enumerate(data[:8]) if bool(v)}
                    if isinstance(data, int):
                        return {i + 1 for i in range(8) if data & (1 << i)}
    except Exception:
        pass

    return set()


# ---------- Party move checks using _asserts (preferred) ----------
def _party_knows_any_move(move_names: Iterable[str]) -> bool:
    """
    Returns True if any party Pokémon knows ANY of the given move names.

    Preferred path uses modules.modes._asserts.assert_has_pokemon_with_any_move,
    which already handles different party representations. We simply wrap it
    and interpret 'no exception' as True.
    """
    names = [str(n).title() for n in move_names if n]

    # Preferred: use the shared assertion helper
    try:
        from modules.modes._asserts import (  # type: ignore
            assert_has_pokemon_with_any_move,
        )

        try:
            # Check current in-RAM party (not the party saved on disk)
            assert_has_pokemon_with_any_move(
                moves=names,
                error_message="__CAPS_CHECK__",  # never shown (we catch exceptions)
                check_in_saved_game=False,
                with_pp_remaining=False,
            )
            return True  # no exception => party has at least one match
        except Exception:
            return False
    except Exception:
        pass

    # Fallback: manual scan across common APIs (very defensive)
    try:
        # Try the canonical helper first
        from modules.pokemon_party import get_party  # type: ignore

        party = get_party()
        if party:
            for mon in party:
                if _mon_knows_any(mon, names):
                    return True
            return False
    except Exception:
        pass

    # Try context fallbacks
    try:
        from modules.context import context  # type: ignore

        for attr in ("party", "pokemon_party", "get_party"):
            obj = getattr(context, attr, None)
            party = obj() if callable(obj) else obj
            if party:
                for mon in party:
                    if _mon_knows_any(mon, names):
                        return True
                return False
    except Exception:
        pass

    return False


def _mon_knows_any(mon, names: Iterable[str]) -> bool:
    names_set = {n.title() for n in names}
    # Common shapes:
    try:
        for mv in getattr(mon, "moves", []):
            n = getattr(mv, "name", None) or getattr(getattr(mv, "move", None), "name", None)
            if isinstance(n, str) and n.title() in names_set:
                return True
    except Exception:
        pass

    for attr in ("move_1", "move_2", "move_3", "move_4"):
        try:
            mv = getattr(mon, attr, None)
            n = getattr(mv, "name", None) if mv else None
            if isinstance(n, str) and n.title() in names_set:
                return True
        except Exception:
            pass

    return False


# ---------- Public API ----------
def compute_capabilities(require_party_move: bool = True) -> Capabilities:
    """
    Snapshot the player’s capabilities right now.

    :param require_party_move:
        - True (default): HM traversal requires the badge AND a party mon
          that knows the corresponding HM move.
        - False: Only the badge gate is required (old/looser behavior).
    """
    badges = _read_badges()

    # Key items
    has_old = _bag_has("Old Rod")
    has_good = _bag_has("Good Rod")
    has_super = _bag_has("Super Rod")
    has_mach = _bag_has("Mach Bike")
    has_acro = _bag_has("Acro Bike")

    # Badge predicate
    b = lambda i: i in badges

    # Move predicate
    knows = (lambda key: True) if not require_party_move else (
        lambda key: _party_knows_any_move(HM_NAMES[key])
    )

    # Badge gates by convention (Emerald)
    can_cut        = b(1) and knows("cut")
    can_flash      = b(2) and knows("flash")
    can_rock_smash = b(3) and knows("rock_smash")
    can_strength   = b(4) and knows("strength")
    can_surf       = b(5) and knows("surf")
    can_fly        = b(6) and knows("fly")
    can_dive       = b(7) and knows("dive")
    can_waterfall  = b(8) and knows("waterfall")

    caps = Capabilities(
        badges=badges,
        has_old_rod=has_old,
        has_good_rod=has_good,
        has_super_rod=has_super,
        has_mach_bike=has_mach,
        has_acro_bike=has_acro,
        has_any_rod=has_old or has_good or has_super,
        can_cut=can_cut,
        can_flash=can_flash,
        can_rock_smash=can_rock_smash,
        can_strength=can_strength,
        can_surf=can_surf,
        can_fly=can_fly,
        can_dive=can_dive,
        can_waterfall=can_waterfall,
    )

    _dbg(
        f"[Caps] badges={sorted(caps.badges)} "
        f"rods=({caps.has_old_rod},{caps.has_good_rod},{caps.has_super_rod}) "
        f"bikes=({caps.has_mach_bike},{caps.has_acro_bike}) "
        f"HM(cut={caps.can_cut}, flash={caps.can_flash}, rock_smash={caps.can_rock_smash}, "
        f"strength={caps.can_strength}, surf={caps.can_surf}, fly={caps.can_fly}, "
        f"dive={caps.can_dive}, waterfall={caps.can_waterfall})"
    )

    return caps


def refresh_capabilities(require_party_move: bool = True) -> Capabilities:
    """
    Compute and cache capabilities (returns the snapshot).
    Call this when you change party or pick up a badge/key item.
    """
    global _CAPS_CACHE
    _CAPS_CACHE = compute_capabilities(require_party_move=require_party_move)
    return _CAPS_CACHE


def get_cached_capabilities() -> Optional[Capabilities]:
    """Return the last snapshot from refresh_capabilities(), or None."""
    return _CAPS_CACHE
