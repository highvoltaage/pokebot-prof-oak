# -*- coding: utf-8 -*-
# plugins/prof_oak_mode.py
#
# Prof Oak mode — wraps a configurable base bot mode (default Level Grind).
# Configure inside this file, optionally prompt once, and persist to plugins/ProfOak/config.json.
#
# Quick config:
#   PLUGIN_DEFAULT_BASES = ["LevelGrind"]   # or ["Spin", "LevelGrind"] etc.
#   ASK_ON_FIRST_USE = False                # True => one-time console prompt
#
# Optional env override:
#   PROFOAK_BASE="Spin"            # or "Spin,LevelGrind"
#
# Adding more bases later:
#   1) Put the user-facing name in PLUGIN_DEFAULT_BASES or via env.
#   2) If needed, add an import mapping in _try_import_candidates().

from __future__ import annotations
from typing import Iterable, Generator, TYPE_CHECKING, Optional, Sequence, Dict, Any
import json
import os
import sys
from pathlib import Path

from modules.plugin_interface import BotPlugin
from modules.context import context
from modules.runtime import get_base_path

if TYPE_CHECKING:
    from modules.modes import BotMode  # typing only

# ======================== Inline config ========================
# Try these base modes in order if nothing else is set.
# Valid examples: "Level Grind", "Spin"
PLUGIN_DEFAULT_BASES: list[str] = ["Spin"]  # e.g., ["Spin", "Level Grind"]

# Ask once on first use (console prompt). The choice is saved to plugins/ProfOak/config.json
ASK_ON_FIRST_USE: bool = False

# ===============================================================

# ---- paths (persist choice here so you don't get asked again) ----
PROFOAK_DIR = get_base_path() / "plugins" / "ProfOak"
PROFOAK_DIR.mkdir(parents=True, exist_ok=True)
CONFIG_PATH = PROFOAK_DIR / "config.json"

# ---------------- Tiny logging helpers ----------------
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

# ---------------- Convenience ----------------
def _norm_name(s: str) -> str:
    return s.strip().lower().replace(" ", "").replace("_", "-")

def _parse_bases(value: object) -> list[str]:
    """Accept str ('Spin,LevelGrind') or list/tuple; return cleaned list."""
    names: list[str] = []
    if isinstance(value, str):
        parts = [p.strip() for p in value.split(",") if p.strip()]
        names = parts or [value.strip()]
    elif isinstance(value, (list, tuple)):
        for v in value:
            if isinstance(v, str) and v.strip():
                names.append(v.strip())
    # de-dup while preserving order
    seen = set(); out = []
    for n in names:
        k = _norm_name(n)
        if k and k not in seen:
            seen.add(k); out.append(n)
    return out

def _load_saved_choice() -> list[str] | None:
    try:
        if CONFIG_PATH.exists():
            data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                if "base_modes" in data:
                    return _parse_bases(data["base_modes"])
                if "base_mode" in data:
                    return _parse_bases(data["base_mode"])
    except Exception:
        pass
    return None

def _save_choice(bases: list[str]) -> None:
    try:
        payload = {"base_modes": bases}
        CONFIG_PATH.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        _log_info(f"[ProfOak] Saved base selection to {CONFIG_PATH.name}: {bases}")
    except Exception as e:
        _log_warn(f"[ProfOak] Could not save base selection: {e}")

# ---------------- Discovery helpers ----------------
def _try_import_candidates(canon: str):
    """
    Try typical import paths for a given canonical name, return class or None.
    canon examples: 'levelgrind', 'spin'
    """
    name_map = {
        "levelgrind": ("level_grind", "LevelGrind"),
        "spin": ("spin", "Spin"),
        # Add more here later, e.g.:
        # "sweetscent": ("sweet_scent", "SweetScent"),
    }
    mod_name, class_name = name_map.get(canon, (canon, canon.capitalize()))

    # 1) built-in modes
    try:
        m = __import__(f"modules.built_in_modes.{mod_name}", fromlist=[class_name])
        return getattr(m, class_name, None)
    except Exception:
        pass

    # 2) regular modes
    try:
        m = __import__(f"modules.modes.{mod_name}", fromlist=[class_name])
        return getattr(m, class_name, None)
    except Exception:
        pass

    return None

def _find_mode_in_registry(canon: str):
    """Search the bot's registered modes by normalized name."""
    try:
        from modules.modes import get_bot_modes  # type: ignore
        for cls in get_bot_modes():
            if cls is None:
                continue
            # Check static .name()
            mode_name = None
            try:
                name_fn = getattr(cls, "name", None)
                mode_name = name_fn() if callable(name_fn) else None
            except Exception:
                mode_name = None

            cand = (
                _norm_name(str(mode_name)) if isinstance(mode_name, str) else None
            ) or _norm_name(getattr(cls, "__name__", ""))

            if cand == canon:
                return cls

            # tolerant variants
            if canon == "levelgrind" and cand in {"level_grind", "levelgrindmode"}:
                return cls
            if canon == "spin" and cand in {"spinner", "spinmode"}:
                return cls
    except Exception:
        pass
    return None

def _discover_available(basenames: list[str]) -> Dict[str, Any]:
    """Return {pretty_name: class} for any of the requested basenames that are available."""
    out: Dict[str, Any] = {}
    for raw in basenames:
        canon = _norm_name(raw)
        cls = _try_import_candidates(canon) or _find_mode_in_registry(canon)
        if cls:
            out[raw] = cls
    return out

def _resolve_base_class(preferred: Sequence[str]):
    """Return (BaseClass, chosen_pretty_name)."""
    for raw in preferred:
        canon = _norm_name(raw)
        cls = _try_import_candidates(canon) or _find_mode_in_registry(canon)
        if cls:
            return cls, raw
    return None, None

# ---------------- Selection logic ----------------
def _get_preferred_bases() -> list[str]:
    # 1) saved choice
    saved = _load_saved_choice()
    if saved:
        return saved

    # 2) env var
    env = os.getenv("PROFOAK_BASE")
    if env and env.strip():
        bases = _parse_bases(env)
        if bases:
            return bases

    # 3) inline defaults
    return list(PLUGIN_DEFAULT_BASES)

def _maybe_prompt_once(defaults: list[str]) -> list[str]:
    """If ASK_ON_FIRST_USE and interactive terminal, prompt once and persist."""
    if not ASK_ON_FIRST_USE:
        return defaults
    # already saved? skip
    if CONFIG_PATH.exists():
        return defaults
    # only prompt if interactive console
    if not sys.stdin or not sys.stdin.isatty():
        return defaults

    avail = _discover_available(["LevelGrind", "Spin"])
    if not avail:
        return defaults

    print("\n[ProfOak] Pick a base mode to wrap:")
    options = list(avail.keys())  # pretty names as discovered
    for i, name in enumerate(options, 1):
        print(f"  {i}) {name}")
    print(f"Press ENTER for default [{defaults[0]}].")
    try:
        choice = input("> ").strip()
    except Exception:
        choice = ""
    if choice.isdigit():
        idx = int(choice) - 1
        if 0 <= idx < len(options):
            chosen = options[idx]
            bases = [chosen] + [b for b in defaults if _norm_name(b) != _norm_name(chosen)]
            _save_choice(bases)
            return bases
    # ENTER or invalid -> keep defaults (and save so we don't ask again)
    _save_choice(defaults)
    return defaults

# ---------------- Plugin that registers our mode ----------------
class ProfOakPlugin(BotPlugin):
    name = "ProfOakPlugin"
    version = "0.5.0-alpha.0"
    author = "HighVoltaage"
    description = "Adds the 'Prof Oak' mode that wraps a configurable base mode (e.g., Level Grind or Spin)."

    def get_additional_bot_modes(self) -> Iterable[type["BotMode"]]:
        preferred = _get_preferred_bases()
        preferred = _maybe_prompt_once(preferred)  # may update & persist on first use

        Base, chosen = _resolve_base_class(preferred)
        if Base is None:
            _log_warn(f"[ProfOak] Could not resolve any base from: {preferred}. Not registering Prof Oak.")
            return []

        pretty = chosen or getattr(Base, "__name__", "UnknownBase")
        _log_info(f"[ProfOak] Registering Prof Oak (wrapping base: {pretty}).")

        class ProfOakMode(Base):  # type: ignore[misc]
            @staticmethod
            def name() -> str:
                return "Prof Oak"

            @staticmethod
            def description() -> str:
                return (f"Prof Oak challenge wrapper using {pretty} behavior. "
                        "Pairs with ShinyQuota to pause when route/method shiny quota is met.")

            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)  # type: ignore
                _log_info(f"[ProfOak] Mode initialized (base: {pretty}).")

            def run(self) -> "Generator":  # type: ignore[override]
                _log_info(f"[ProfOak] Starting run loop (delegating to {pretty}).")
                yield from super().run()  # type: ignore

        return [ProfOakMode]
