# -*- coding: utf-8 -*-
# plugins/prof_oak_mode.py
#
# Registers TWO modes:
#   • Prof Oak — wraps a configurable base bot mode (default LevelGrind/Spin per your settings)
#   • Living Prof Oak — attempts to enable "living dex" behavior in shiny_quota; if unavailable,
#                       it transparently falls back to Prof Oak behavior.
#
# Configure inside this file, optionally prompt once, and persist to plugins/ProfOak/config.json.
#
# Quick config:
#   PLUGIN_DEFAULT_BASES = ["LevelGrind"]   # or ["Spin", "LevelGrind"] etc.
#   ASK_ON_FIRST_USE = False                # True => one-time console prompt
#
# Optional env override:
#   PROFOAK_BASE="Spin"            # or "Spin,LevelGrind"

from __future__ import annotations
from typing import Iterable, Generator, TYPE_CHECKING, Optional, Sequence, Dict, Any, Tuple, Type
import json
import os
import sys
import importlib
from pathlib import Path

from modules.plugin_interface import BotPlugin
from modules.context import context
from modules.runtime import get_base_path

if TYPE_CHECKING:
    from modules.modes import BotMode  # typing only

# ======================== Inline config ========================
PLUGIN_DEFAULT_BASES: list[str] = ["Spin"]  # e.g., ["Spin", "LevelGrind"]
ASK_ON_FIRST_USE: bool = False

# ===============================================================

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
    names: list[str] = []
    if isinstance(value, str):
        parts = [p.strip() for p in value.split(",") if p.strip()]
        names = parts or [value.strip()]
    elif isinstance(value, (list, tuple)):
        for v in value:
            if isinstance(v, str) and v.strip():
                names.append(v.strip())
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
    name_map = {
        "levelgrind": ("level_grind", "LevelGrind"),
        "spin": ("spin", "Spin"),
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
    try:
        from modules.modes import get_bot_modes  # type: ignore
        for cls in get_bot_modes():
            if cls is None:
                continue
            mode_name = None
            try:
                name_fn = getattr(cls, "name", None)
                mode_name = name_fn() if callable(name_fn) else None
            except Exception:
                mode_name = None

            cand = (_norm_name(str(mode_name)) if isinstance(mode_name, str) else None) or _norm_name(getattr(cls, "__name__", ""))

            if cand == canon:
                return cls
            if canon == "levelgrind" and cand in {"level_grind", "levelgrindmode"}:
                return cls
            if canon == "spin" and cand in {"spinner", "spinmode"}:
                return cls
    except Exception:
        pass
    return None

def _discover_available(basenames: list[str]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for raw in basenames:
        canon = _norm_name(raw)
        cls = _try_import_candidates(canon) or _find_mode_in_registry(canon)
        if cls:
            out[raw] = cls
    return out

def _resolve_base_class(preferred: Sequence[str]) -> Tuple[Optional[type], Optional[str]]:
    for raw in preferred:
        canon = _norm_name(raw)
        cls = _try_import_candidates(canon) or _find_mode_in_registry(canon)
        if cls:
            return cls, raw
    return None, None

# ---------------- Selection logic ----------------
def _get_preferred_bases() -> list[str]:
    saved = _load_saved_choice()
    if saved:
        return saved
    env = os.getenv("PROFOAK_BASE")
    if env and env.strip():
        bases = _parse_bases(env)
        if bases:
            return bases
    return list(PLUGIN_DEFAULT_BASES)

def _maybe_prompt_once(defaults: list[str]) -> list[str]:
    if not ASK_ON_FIRST_USE:
        return defaults
    if CONFIG_PATH.exists():
        return defaults
    if not sys.stdin or not sys.stdin.isatty():
        return defaults

    avail = _discover_available(["LevelGrind", "Spin"])
    if not avail:
        return defaults

    print("\n[ProfOak] Pick a base mode to wrap:")
    options = list(avail.keys())
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
    _save_choice(defaults)
    return defaults

# ---------------- Talk to shiny_quota ----------------
def _get_shiny_quota_instance():
    try:
        sq_mod = importlib.import_module("plugins.shiny_quota")
    except Exception:
        return None, None
    SQ = getattr(sq_mod, "ShinyQuotaPlugin", None)
    if SQ is None:
        return None, None
    inst = None
    try:
        from modules.plugins import get_plugin_instance  # type: ignore
        inst = get_plugin_instance(SQ)
    except Exception:
        try:
            plist = getattr(context, "plugins", [])
            for p in plist or []:
                if isinstance(p, SQ):
                    inst = p; break
        except Exception:
            pass
    return SQ, inst

def _configure_shiny_quota(living: bool) -> bool:
    """
    Try to flip shiny_quota into (or out of) living-dex mode and force-refresh its caches.
    Returns True if we likely succeeded.
    """
    SQ, inst = _get_shiny_quota_instance()
    if SQ is None or inst is None:
        _log_warn("[ProfOak] shiny_quota not loaded; continuing anyway.")
        return False

    # Try explicit setter first
    for setter in ("set_livingdex_enabled", "set_living_dex", "enable_living_dex", "enable_livingdex"):
        fn = getattr(inst, setter, None)
        if callable(fn):
            try:
                fn(bool(living))
                break
            except Exception:
                pass
    else:
        # Try mode-style setter as a fallback
        for setter in ("set_mode", "set_quota_mode"):
            fn = getattr(inst, setter, None)
            if callable(fn):
                try:
                    fn("LIVING" if living else "STANDARD")
                    break
                except Exception:
                    pass

    # Force-refresh if possible so overlay text updates immediately
    for refresh_name in ("force_refresh",):
        rf = getattr(inst, refresh_name, None)
        if callable(rf):
            try:
                rf()
            except Exception:
                pass

    # Not strictly verifiable; return True if we had a path to act.
    return True

# ---------------- Mode factory ----------------
def _make_wrapped_mode(Base: Type, pretty: str, living: bool) -> Type["BotMode"]:
    mode_name = "Living Prof Oak" if living else "Prof Oak"

    class _Wrapped(Base):  # type: ignore[misc]
        @staticmethod
        def name() -> str:
            return mode_name

        @staticmethod
        def description() -> str:
            if living:
                return ("Living Prof Oak wrapper using "
                        f"{pretty} behavior. Attempts to require a shiny for each evolution stage "
                        "via ShinyQuota; if not supported, falls back to standard Prof Oak.")
            return (f"Prof Oak challenge wrapper using {pretty} behavior. "
                    "Pairs with ShinyQuota to pause when route/method shiny quota is met.")

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)  # type: ignore
            if living:
                _configure_shiny_quota(True)
                _log_info("[Living Prof Oak] Living-dex mode active (requested).")
            else:
                _configure_shiny_quota(False)
                _log_info(f"[Prof Oak] Mode initialized (base: {pretty}).")

        def run(self) -> "Generator":  # type: ignore[override]
            if living:
                _log_info(f"[Living Prof Oak] Starting run loop (delegating to {pretty}).")
            else:
                _log_info(f"[Prof Oak] Starting run loop (delegating to {pretty}).")
            yield from super().run()  # type: ignore

    return _Wrapped

# ---------------- Plugin that registers both modes ----------------
class ProfOakPlugin(BotPlugin):
    name = "ProfOakPlugin"
    version = "0.6.1-alpha.0"
    author = "HighVoltaage"
    description = ("Adds 'Prof Oak' and 'Living Prof Oak' modes that wrap a configurable base mode "
                   "(e.g., LevelGrind or Spin). Living Prof Oak flips shiny_quota's living-dex flag "
                   "on enter; Prof Oak flips it off.")

    def get_additional_bot_modes(self) -> Iterable[type["BotMode"]]:
        preferred = _get_preferred_bases()
        preferred = _maybe_prompt_once(preferred)

        Base, chosen = _resolve_base_class(preferred)
        if Base is None:
            _log_warn(f"[ProfOak] Could not resolve any base from: {preferred}. Not registering Prof Oak modes.")
            return []

        pretty = chosen or getattr(Base, "__name__", "UnknownBase")
        _log_info(f"[ProfOak] Registering Prof Oak modes (base: {pretty}).")

        ProfOakMode = _make_wrapped_mode(Base, pretty, living=False)
        LivingProfOakMode = _make_wrapped_mode(Base, pretty, living=True)

        return [ProfOakMode, LivingProfOakMode]
