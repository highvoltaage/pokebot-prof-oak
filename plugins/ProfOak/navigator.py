# plugins/ProfOak/navigator.py
from __future__ import annotations

import json
import random
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

try:
    from modules.player import (
        player_avatar_is_controllable as _engine_is_controllable,
        player_avatar_is_standing_still as _engine_is_standing_still,
    )
except Exception:
    _engine_is_controllable = None
    _engine_is_standing_still = None

try:
    from modules.tasks import is_waiting_for_input
except Exception:
    def is_waiting_for_input() -> bool:
        return False


def _log_info(ctx, msg: str) -> None:
    lg = getattr(ctx, "logger", None) or getattr(ctx, "log", None)
    if lg and hasattr(lg, "info"):
        try: lg.info(msg); return
        except Exception: pass
    print(f"[Navigator] {msg}")


def _log_warn(ctx, msg: str) -> None:
    lg = getattr(ctx, "logger", None) or getattr(ctx, "log", None)
    for m in ("warning", "warn"):
        if lg and hasattr(lg, m):
            try: getattr(lg, m)(msg); return
            except Exception: pass
    print(f"WARNING: [Navigator] {msg}")


def _profoak_dir(ctx) -> Path:
    try:
        from modules.runtime import get_base_path
        return get_base_path() / "plugins" / "ProfOak"
    except Exception:
        return Path("plugins/ProfOak")


def _json_dir(ctx) -> Path:
    base = _profoak_dir(ctx)
    for name in ("JSON", "json"):
        candidate = base / name
        if candidate.exists():
            return candidate
    return base / "JSON"


def _load_route_order(ctx) -> List[Dict[str, Any]]:
    path = _json_dir(ctx) / "emerald_route_order.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            raise ValueError("route order json root must be a list")
        _log_info(ctx, f"loaded route order with {len(data)} entries from {path}")
        return data
    except Exception as e:
        _log_warn(ctx, f"could not load route order ({e}); navigation disabled")
        return []


_norm_rx = re.compile(r"[^A-Z0-9]+")
def _norm_name(s: str) -> str:
    s = (s or "").upper()
    s = re.sub(r"^MAPSEC[_:\-\s]*", "", s)
    return _norm_rx.sub("", s)


def _extract_group_number(entry: Dict[str, Any]) -> Tuple[Optional[int], Optional[int]]:
    g = None; n = None
    for gk in ("group", "map_group", "mapGroup"):
        if gk in entry: g = int(entry[gk]); break
    for nk in ("number", "map_number", "mapNumber"):
        if nk in entry: n = int(entry[nk]); break
    if (g is None or n is None) and isinstance(entry.get("map"), dict):
        m = entry["map"]
        if g is None:
            for gk in ("group", "map_group", "mapGroup"):
                if gk in m: g = int(m[gk]); break
        if n is None:
            for nk in ("number", "map_number", "mapNumber"):
                if nk in m: n = int(m[nk]); break
    if (g is None or n is None):
        _id_regex = re.compile(r"^\s*(\d+)\D+(\d+)\s*$")
        for kk in ("map_key", "key", "id"):
            if kk in entry:
                mm = _id_regex.match(str(entry[kk]))
                if mm:
                    g = int(mm.group(1)); n = int(mm.group(2)); break
    return g, n


def _extract_coords(entry: Dict[str, Any]) -> Optional[Tuple[int, int]]:
    for ck in ("coords", "xy", "position"):
        v = entry.get(ck)
        if isinstance(v, (list, tuple)) and len(v) == 2:
            try: return (int(v[0]), int(v[1]))
            except Exception: pass
    return None


def _resolve_current_index(current_map: Any, order: List[Dict[str, Any]], ctx) -> Optional[int]:
    if isinstance(current_map, (tuple, list)) and len(current_map) == 2:
        g, n = current_map
        if isinstance(g, int) and isinstance(n, int):
            for i, e in enumerate(order):
                gg, nn = _extract_group_number(e)
                if gg == g and nn == n:
                    return i
    if isinstance(current_map, str):
        key = _norm_name(current_map)
        for i, e in enumerate(order):
            if _norm_name(str(e.get("name", ""))) == key:
                return i
            for alt in e.get("alt", []):
                if _norm_name(str(alt)) == key:
                    return i
    try:
        g = getattr(ctx, "map_group", None) or getattr(getattr(ctx, "map", None), "group", None)
        n = getattr(ctx, "map_number", None) or getattr(getattr(ctx, "map", None), "number", None)
        if isinstance(g, int) and isinstance(n, int):
            for i, e in enumerate(order):
                gg, nn = _extract_group_number(e)
                if gg == g and nn == n:
                    return i
    except Exception:
        pass
    return None


_GRASS_SET = {"GRASS", "TALL_GRASS", "LAND", "FIELD", "GRASSPATCH"}
_WATER_SET = {"WATER", "SEA", "OCEAN", "SURF"}
_ROCK_SET  = {"ROCK", "ROCK_SMASH", "BREAKABLE_ROCK", "ROCKSMASH"}


def _label(o: Any) -> str:
    return str(o).strip().replace("-", "_").replace(" ", "_").upper()


def _is_viable_tile_for_method(tile: Any, method: str) -> bool:
    m = _label(method)
    if hasattr(tile, "tiletype"): raw = getattr(tile, "tiletype")
    elif hasattr(tile, "tile_type"): raw = getattr(tile, "tile_type")
    elif hasattr(tile, "behavior"): raw = getattr(tile, "behavior")
    elif hasattr(tile, "name"): raw = getattr(tile, "name")
    elif isinstance(tile, dict): raw = tile.get("tiletype") or tile.get("type") or tile.get("behavior") or tile.get("name")
    else: raw = tile
    t = _label(raw)
    has_enc = getattr(tile, "has_encounters", True)
    if isinstance(has_enc, bool) and not has_enc: return False
    if m in ("GRASS", "LAND"): return (t in _GRASS_SET) and (t not in _WATER_SET)
    if m in ("SURF", "WATER", "ROD", "FISHING"): return t in _WATER_SET
    if m in ("ROCK_SMASH", "ROCKSMASH", "ROCK"): return t in _ROCK_SET
    return t not in {"WALL", "BLOCK", "IMPASSABLE"}


def _try_get_matrix_or_size(group: int, number: int) -> Tuple[Optional[Sequence[Sequence[Any]]], Optional[Tuple[int,int]]]:
    import modules.map as m
    for name in ("get_map_tile_matrix","get_map_tiles","read_map_tiles","get_block_matrix","get_map_block_map"):
        if hasattr(m, name):
            try:
                mat = getattr(m, name)(group, number)
                if mat:
                    h = len(mat); w = len(mat[0]) if h else 0
                    return mat, (w, h) if w and h else None
            except Exception:
                pass
    for name in ("get_map_layout","get_map_layout_by_group_and_number","get_map_layout_by_id"):
        if hasattr(m, name):
            try:
                lay = getattr(m, name)(group, number)
                if lay is not None:
                    if isinstance(lay, dict):
                        w = lay.get("width") or lay.get("w")
                        h = lay.get("height") or lay.get("h")
                        if lay.get("tiles"): return lay["tiles"], (int(w), int(h)) if w and h else None
                        if w and h: return None, (int(w), int(h))
                    w = getattr(lay, "width", None); h = getattr(lay, "height", None)
                    if hasattr(lay, "tiles"): return getattr(lay, "tiles"), (int(w), int(h)) if w and h else None
                    if w and h: return None, (int(w), int(h))
            except Exception:
                pass
    for name in ("get_map_info","get_map_header","get_map_headers"):
        if hasattr(m, name):
            try:
                info = getattr(m, name)(group, number)
                if info:
                    if isinstance(info, dict):
                        w = info.get("width") or info.get("w")
                        h = info.get("height") or info.get("h")
                        if w and h: return None, (int(w), int(h))
                    else:
                        w = getattr(info, "width", None); h = getattr(info, "height", None)
                        if w and h: return None, (int(w), int(h))
            except Exception:
                pass
    return None, None


def _safe_center(size: Optional[Tuple[int,int]]) -> Tuple[int,int]:
    if not size: return (10,10)
    w,h = size
    x = max(1, min(w-2, w//2)); y = max(1, min(h-2, h//2))
    return (x,y)


def _pick_encounter_coordinate(ctx, group: int, number: int, method: str) -> Tuple[int,int]:
    try:
        import modules.map as m  # noqa: F401
        from modules.map import get_map_data
    except Exception:
        _log_warn(ctx, "modules.map not importable; using default coords")
        return (10,10)

    matrix, size = _try_get_matrix_or_size(group, number)
    if matrix is not None:
        cands: List[Tuple[int,int]] = []
        try:
            h = len(matrix); w = len(matrix[0]) if h else 0
            for y in range(h):
                row = matrix[y]
                for x in range(w):
                    tile = row[x]
                    if _is_viable_tile_for_method(tile, method):
                        cands.append((x,y))
        except Exception as e:
            _log_warn(ctx, f"error scanning tile matrix ({e}); using safe center")
            return _safe_center(size)
        if cands:
            coord = random.choice(cands)
            _log_info(ctx, f"picked encounter tile for {method}: {coord} (from {len(cands)} candidates)")
            return coord
        _log_warn(ctx, f"no viable tiles found for method={method}; using safe center")
        return _safe_center(size)

    if size is None:
        w,h = 60,60
        _log_info(ctx, "no matrix/size available; scanning a 60×60 area for viable encounter tiles")
    else:
        w,h = size

    cands: List[Tuple[int,int]] = []
    try:
        from modules.map import get_map_data
        for y in range(h):
            for x in range(w):
                try:
                    tile = get_map_data((group, number), (x,y))
                except Exception:
                    continue
                if _is_viable_tile_for_method(tile, method):
                    cands.append((x,y))
    except Exception as e:
        _log_warn(ctx, f"error iterating tiles ({e}); using safe center")
        return _safe_center(size)

    if cands:
        coord = random.choice(cands)
        _log_info(ctx, f"picked encounter tile for {method}: {coord} (from {len(cands)} candidates)")
        return coord
    _log_warn(ctx, f"no viable tiles found for method={method}; using safe center")
    return _safe_center(size)


def _is_controllable(context) -> bool:
    if _engine_is_controllable:
        try: return bool(_engine_is_controllable())
        except Exception: pass
    for fn_name in ("is_player_control_enabled","is_controllable","can_control"):
        fn = getattr(context, fn_name, None)
        if callable(fn):
            try: return bool(fn())
            except Exception: pass
    try:
        player = getattr(context, "player", None)
        fn = getattr(player, "is_controllable", None)
        if callable(fn): return bool(fn())
    except Exception:
        pass
    return True


def _press_button(context, button: str = "A") -> None:
    emu = getattr(context, "emulator", None)
    if not emu: return
    for name in ("tap","tap_button","press_button","press_once"):
        fn = getattr(emu, name, None)
        if callable(fn):
            try: fn(button); return
            except Exception: pass
    set_held = getattr(emu, "set_held_buttons", None)
    if callable(set_held):
        try: set_held({button: True})
        except Exception: pass


def _mash_dialog_once(context) -> None:
    _press_button(context, "A")
    _press_button(context, "B")


def _dismiss_dialog(context, max_frames: int = 600):
    frames = 0
    try:
        while is_waiting_for_input() and frames < max_frames:
            _press_button(context, "A")
            if (frames % 8) == 0:
                _press_button(context, "B")
            frames += 1
            yield
    except Exception:
        return


def _wait_until_controllable(context):
    try:
        from modules.memory import get_game_state, GameState
    except Exception:
        get_game_state = None; GameState = None

    yield from _dismiss_dialog(context)

    while True:
        ok_overworld = True
        if get_game_state and GameState:
            try: ok_overworld = (get_game_state() == GameState.OVERWORLD)
            except Exception: ok_overworld = True

        if ok_overworld and _is_controllable(context):
            break
        _mash_dialog_once(context)
        yield

    if _engine_is_standing_still:
        for _ in range(5):
            try:
                if _engine_is_standing_still(): break
            except Exception: pass
            yield


def _current_map_id() -> Optional[Tuple[int,int]]:
    try:
        from modules.map import get_map_data_for_current_position
        loc = get_map_data_for_current_position()
        if loc:
            return (int(getattr(loc, "map_group")), int(getattr(loc, "map_number")))
    except Exception:
        pass
    return None


def _get_player_xy() -> Optional[Tuple[int,int]]:
    try:
        from modules.map import get_map_data_for_current_position
        loc = get_map_data_for_current_position()
        if loc and getattr(loc, "local_coordinates", None):
            x,y = loc.local_coordinates
            return (int(x), int(y))
    except Exception:
        pass
    return None


def _list_warp_tiles_to_target(target_group: int, target_number: int) -> List[Tuple[int,int]]:
    out: List[Tuple[int,int,int]] = []
    try:
        from modules.map import get_map_data_for_current_position
        cur = get_map_data_for_current_position()
        if not cur: return []
        px,py = _get_player_xy() or (0,0)
        for w in getattr(cur, "warps", []) or []:
            try:
                dg = int(getattr(w, "destination_map_group"))
                dn = int(getattr(w, "destination_map_number"))
                if dg == int(target_group) and dn == int(target_number):
                    xy = getattr(w, "local_coordinates", None)
                    if isinstance(xy, (tuple, list)) and len(xy) == 2:
                        wx,wy = int(xy[0]), int(xy[1])
                        dist = abs(wx - px) + abs(wy - py)
                        out.append((wx, wy, dist))
            except Exception:
                continue
    except Exception:
        return []
    out.sort(key=lambda t: t[2])
    return [(x, y) for (x, y, _d) in out]


def _list_all_warps_from_current() -> List[Tuple[int,int,Tuple[int,int]]]:
    results: List[Tuple[int,int,Tuple[int,int]]] = []
    try:
        from modules.map import get_map_data_for_current_position
        cur = get_map_data_for_current_position()
        if not cur: return results
        for w in getattr(cur, "warps", []) or []:
            try:
                dg = int(getattr(w, "destination_map_group"))
                dn = int(getattr(w, "destination_map_number"))
                xy = getattr(w, "local_coordinates", None)
                if isinstance(xy, (tuple, list)) and len(xy) == 2:
                    wx,wy = int(xy[0]), int(xy[1])
                    results.append((dg, dn, (wx, wy)))
            except Exception:
                continue
    except Exception:
        pass
    return results


def _wait_for_map_change(context, to_group: int, to_number: int, max_frames: int = 1200):
    cur0 = _current_map_id()
    if cur0 == (int(to_group), int(to_number)):
        return

    frames = 0
    while True:
        cur = _current_map_id()
        if cur and cur == (int(to_group), int(to_number)):
            break
        yield from _dismiss_dialog(context)
        if (frames % 4) == 0: _press_button(context, "A")
        if (frames % 12) == 0: _press_button(context, "B")
        frames += 1
        if frames > max_frames:
            _log_warn(context, "Timeout while waiting for map transition; proceeding anyway.")
            break
        yield


def _neighbors_sorted_towards(wx: int, wy: int, prefer_from: Optional[Tuple[int,int]]):
    choices = [(wx, wy-1), (wx, wy+1), (wx-1, wy), (wx+1, wy)]
    if prefer_from is None: return choices
    px,py = prefer_from
    return sorted(choices, key=lambda c: abs(c[0]-px) + abs(c[1]-py))


def _approach_then_step_into_warp(context, navigate_to, cur_ids, warp_xy) -> bool:
    wx,wy = warp_xy
    prefer = _get_player_xy()
    for nb in _neighbors_sorted_towards(wx, wy, prefer):
        try:
            _log_info(context, f"Walking to warp-adjacent {nb} …")
            yield from _dismiss_dialog(context)
            yield from navigate_to(cur_ids, nb, run=True,
                                   avoid_encounters=False,
                                   avoid_scripted_events=False,
                                   expecting_script=True)
            for _ in range(3): yield
            _log_info(context, f"Stepping into warp from {nb} -> {(wx, wy)}")
            yield from _dismiss_dialog(context)
            yield from navigate_to(cur_ids, (wx, wy), run=True,
                                   avoid_encounters=False,
                                   avoid_scripted_events=False,
                                   expecting_script=True)
            return True
        except Exception as e:
            _log_warn(context, f"adjacent approach via {nb} failed: {e}")
            continue
    return False


def _step_off_warp_if_needed(context, navigate_to) -> None:
    try:
        from modules.map import get_map_data_for_current_position
        cur = get_map_data_for_current_position()
        pos = _get_player_xy()
        if not cur or not pos:
            return
        px,py = pos
        for w in getattr(cur, "warps", []) or []:
            xy = getattr(w, "local_coordinates", None)
            if isinstance(xy, (tuple, list)) and len(xy) == 2:
                wx,wy = int(xy[0]), int(xy[1])
                if (wx, wy) == (px, py):
                    for nb in _neighbors_sorted_towards(px, py, None):
                        try:
                            yield from navigate_to((int(cur.map_group), int(cur.map_number)), nb,
                                                   run=True,
                                                   avoid_encounters=False,
                                                   avoid_scripted_events=False,
                                                   expecting_script=True)
                            return
                        except Exception:
                            continue
                    return
    except Exception:
        return


def _get_nav_state(context) -> Dict[str, Any]:
    st = getattr(context, "_prof_oak_nav_state", None)
    if st is None:
        st = {}
        setattr(context, "_prof_oak_nav_state", st)
    return st


def navigate_after_quota(
    *,
    context,
    current_map: Any,
    method: str,
    learned: Dict[str, Dict[str, List[str]]],
    owned_counts: Dict[str, int],
):
    order = _load_route_order(context)
    if not order:
        return

    yield from _dismiss_dialog(context)
    yield from _wait_until_controllable(context)

    _log_info(context, f"current_map={current_map!r}, method={method}")

    state = _get_nav_state(context)

    idx = state.get("last_idx")
    if idx is None:
        idx = _resolve_current_index(current_map, order, context)
        if idx is None:
            _log_warn(context, "Could not parse current map; assuming first route in order")
            idx = 0

    L = len(order)
    next_idx = None
    for k in range(1, L + 1):
        cand = order[(idx + k) % L]
        gg, nn = _extract_group_number(cand)
        if gg is not None and nn is not None:
            next_idx = (idx + k) % L
            break
    if next_idx is None:
        _log_warn(context, "No valid (group, number) found in route order; aborting")
        return

    final_entry = order[next_idx]
    FG, FN = _extract_group_number(final_entry)
    final_name = final_entry.get("name", f"{FG}:{FN}")
    explicit_coords = _extract_coords(final_entry)
    _log_info(context, f"Final target -> {final_name} (group={FG}, number={FN})")

    state["target_ids"] = (int(FG), int(FN))

    if explicit_coords is not None:
        dest_xy = (int(explicit_coords[0]), int(explicit_coords[1]))
        _log_info(context, f"using coords provided by route json: {dest_xy}")
    else:
        dest_xy = _pick_encounter_coordinate(context, int(FG), int(FN), method)

    try:
        from modules.modes.util.walking import navigate_to
    except Exception as e:
        _log_warn(context, f"Cannot import walking.navigate_to: {e}")
        return

    # -------- Phase 1: direct try BEFORE manual warp loop --------
    for attempt in range(2):
        yield from _wait_until_controllable(context)
        cur_ids = _current_map_id()
        if cur_ids == (int(FG), int(FN)):
            break
        try:
            _log_info(context, f"Attempting direct navigate_to final {final_name} @ {dest_xy} …")
            yield from _dismiss_dialog(context)
            yield from navigate_to((int(FG), int(FN)), dest_xy, run=True,
                                   avoid_encounters=False,
                                   avoid_scripted_events=False,
                                   expecting_script=True)
        except Exception as e:
            _log_warn(context, f"direct navigate_to final raised: {e}")

    # -------- Phase 1 (fallback): manual warp stepping --------
    last_map_before_step: Optional[Tuple[int,int]] = None
    while True:
        yield from _wait_until_controllable(context)
        cur_ids = _current_map_id()
        if cur_ids == (int(FG), int(FN)):
            break

        # Try a direct path inside the loop as well (fixes 104N -> 116 style cases)
        try:
            _log_info(context, f"Retrying direct navigate_to {final_name} inside loop …")
            yield from _dismiss_dialog(context)
            yield from navigate_to((int(FG), int(FN)), dest_xy, run=True,
                                   avoid_encounters=False,
                                   avoid_scripted_events=False,
                                   expecting_script=True)
        except Exception as e:
            _log_info(context, f"direct in-loop attempt yielded: {e}")
        if _current_map_id() == (int(FG), int(FN)):
            break

        direct_warps = _list_warp_tiles_to_target(int(FG), int(FN))

        step_g, step_n = (int(FG), int(FN))
        if not direct_warps:
            warp_dests = _list_all_warps_from_current()

            if not warp_dests:
                _log_warn(context, "No warps available from current map; trying direct path instead of aborting.")
                try:
                    yield from _dismiss_dialog(context)
                    yield from navigate_to((int(FG), int(FN)), dest_xy, run=True,
                                           avoid_encounters=False,
                                           avoid_scripted_events=False,
                                           expecting_script=True)
                except Exception as e:
                    _log_warn(context, f"direct fallback also raised: {e}")
                if _current_map_id() != (int(FG), int(FN)):
                    _log_warn(context, "Still not on target map; will loop and retry.")
                continue

            if last_map_before_step:
                warp_dests = [(dg, dn, xy) for (dg, dn, xy) in warp_dests if (dg, dn) != last_map_before_step]

            cur_idx = _resolve_current_index(cur_ids, order, context)
            tgt_idx = next_idx
            if cur_idx is None:
                _log_warn(context, "Could not resolve current map index; aborting navigation.")
                return

            best = None
            target_span = (tgt_idx - cur_idx) % L

            for (dg, dn, _xy) in warp_dests:
                dest_idx = _resolve_current_index((dg, dn), order, context)
                if dest_idx is None:
                    continue
                fwd = (dest_idx - cur_idx) % L
                if 0 < fwd <= target_span:
                    if best is None or fwd < best[0]:
                        best = (fwd, (dg, dn))

            if best is None:
                for (dg, dn, _xy) in warp_dests:
                    dest_idx = _resolve_current_index((dg, dn), order, context)
                    if dest_idx is None:
                        continue
                    fwd = (dest_idx - cur_idx) % L
                    if best is None or fwd < best[0]:
                        best = (fwd, (dg, dn))

            if best:
                step_g, step_n = best[1]
                _log_info(context, f"No direct warp. Stepping via intermediate {(step_g, step_n)} toward {final_name}.")
                warp_list = _list_warp_tiles_to_target(step_g, step_n)
            else:
                _log_warn(context, "Could not select an intermediate step; will try direct path instead of aborting.")
                try:
                    yield from _dismiss_dialog(context)
                    yield from navigate_to((int(FG), int(FN)), dest_xy, run=True,
                                           avoid_encounters=False,
                                           avoid_scripted_events=False,
                                           expecting_script=True)
                except Exception:
                    pass
                continue
        else:
            warp_list = direct_warps

        if not warp_list:
            _log_warn(context, "No warp tiles lead to the next step; will try direct path again.")
            try:
                yield from _dismiss_dialog(context)
                yield from navigate_to((int(FG), int(FN)), dest_xy, run=True,
                                       avoid_encounters=False,
                                       avoid_scripted_events=False,
                                       expecting_script=True)
            except Exception:
                pass
            continue

        pos_now = _get_player_xy()
        warp_list = [xy for xy in warp_list if pos_now is None or tuple(xy) != tuple(pos_now)]

        _log_info(context, f"Found {len(warp_list)} warp candidate(s); trying nearest first…")
        reached = False
        for warp_xy in warp_list:
            _log_info(context, f"Attempting warp at local coords {warp_xy} on current map {cur_ids}.")
            try:
                yield from _dismiss_dialog(context)
                last_map_before_step = cur_ids
                yield from navigate_to(cur_ids, warp_xy, run=True,
                                       avoid_encounters=False,
                                       avoid_scripted_events=False,
                                       expecting_script=True)
                reached = True
            except Exception as e:
                _log_warn(context, f"navigate_to (warp step) raised: {e}. Trying adjacent approach…")
                try:
                    last_map_before_step = cur_ids
                    reached = (yield from _approach_then_step_into_warp(context, navigate_to, cur_ids, warp_xy))
                except Exception as e2:
                    _log_warn(context, f"adjacent approach raised: {e2}")
                    reached = False

            if reached:
                _log_info(context, "Stepped on warp; waiting for map transition...")
                yield from _wait_for_map_change(context, int(step_g), int(step_n))
                yield from _step_off_warp_if_needed(context, navigate_to)
                break
            else:
                _log_info(context, "That warp didn’t work; trying the next nearest…")

        if not reached:
            _log_warn(context, "Could not reach any warp tile; will retry loop (and try direct again).")
            continue

    # --- Phase 3: arrive and hand off back to base mode (Spin) ---
    state["last_idx"] = next_idx

    # Remember chosen farm tile for this target (so retries land on the same spot)
    if "farm_xy" in state and state.get("target_ids") == (int(FG), int(FN)):
        dest_xy = tuple(state["farm_xy"])
    else:
        state["farm_xy"] = tuple(dest_xy)

    # Final approach loop: go to the chosen encounter tile (with retry cap)
    max_loops = 2           # stop trying after 10 loops
    loops = 0
    prox_threshold = 1       # consider "arrived" if within 1 tile (Manhattan distance)

    while loops < max_loops:
        yield from _wait_until_controllable(context)

        pos = _get_player_xy()
        if pos:
            dx = abs(pos[0] - dest_xy[0])
            dy = abs(pos[1] - dest_xy[1])
            if (dx + dy) <= prox_threshold:
                _log_info(context, f"Arrived (≤{prox_threshold} tile) at {dest_xy}; navigation complete.")
                break

        _log_info(context, f"Navigating within target map {final_name} to encounter tile {dest_xy} for method={method}... (attempt {loops+1}/{max_loops})")
        try:
            yield from _dismiss_dialog(context)
            yield from navigate_to((int(FG), int(FN)), dest_xy, run=True,
                                avoid_encounters=False,
                                avoid_scripted_events=False,
                                expecting_script=True)
        except Exception as e:
            _log_warn(context, f"navigate_to raised: {e}; will retry after regaining control.")
            for _ in range(10):
                yield

        loops += 1

    if loops >= max_loops:
        _log_warn(context, f"Final approach retries exceeded ({max_loops}); handing control back anyway.")


    # Nudge: do a tiny out-and-back so the engine's controller stack fully clears and Spin resumes.
    try:
        cur_ids = _current_map_id()
        px, py = _get_player_xy() or dest_xy
        nx, ny = (max(1, px - 1), py)  # pick a safe neighbor
        if cur_ids == (int(FG), int(FN)):
            _log_info(context, "Nudging control (one-tile out-and-back) to hand off to Spin…")
            yield from navigate_to(cur_ids, (nx, ny), run=False,
                                   avoid_encounters=False,
                                   avoid_scripted_events=False,
                                   expecting_script=False)
            yield from navigate_to(cur_ids, (px, py), run=False,
                                   avoid_encounters=False,
                                   avoid_scripted_events=False,
                                   expecting_script=False)
    except Exception as e:
        _log_warn(context, f"Nudge failed (safe to ignore): {e}")

    # Make absolutely sure nothing is being held down.
    try:
        emu = getattr(context, "emulator", None)
        if emu and hasattr(emu, "reset_held_buttons"):
            emu.reset_held_buttons()
    except Exception:
        pass

    # Cleanup transient state; returning from the generator gives control back to base mode.
    state.pop("farm_xy", None)
    state.pop("target_ids", None)
    _log_info(context, "Handoff complete; base mode should resume.")
