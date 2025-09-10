# plugins/prof_oak_mode.py
# -*- coding: utf-8 -*-
"""
Registers TWO modes:
  • Prof Oak — wraps a configurable base bot mode (e.g., Spin or LevelGrind)
  • Living Prof Oak — same wrapper, but flips ShinyQuota to Living-Dex

This module also ensures the ShinyQuota plugin (now under plugins/ProfOak/)
is imported AND properly registered into the bot's global plugin registry.
"""

from __future__ import annotations

from typing import Iterable, TYPE_CHECKING, Optional, Sequence, Dict, Any, Tuple, Type
import importlib
import json
import os
import sys
from pathlib import Path

from modules.plugin_interface import BotPlugin
from modules.context import context
from modules.runtime import get_base_path

if TYPE_CHECKING:
    from modules.modes import BotMode  # typing only


__all__ = ["ProfOakPlugin"]
__version__ = "0.3.1"  # keep in lockstep with ShinyQuota


# ──────────────────────────────────────────────────────────────────────────────
# CONFIG
# ──────────────────────────────────────────────────────────────────────────────
PLUGIN_DEFAULT_BASES: list[str] = ["Spin", "LevelGrind"]
ASK_ON_FIRST_USE: bool = False  # set True to prompt in TTY once

PROFOAK_DIR = get_base_path() / "plugins" / "ProfOak"
PROFOAK_DIR.mkdir(parents=True, exist_ok=True)
CONFIG_PATH = PROFOAK_DIR / "config.json"


# ──────────────────────────────────────────────────────────────────────────────
# Logging helpers
# ──────────────────────────────────────────────────────────────────────────────
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


# ──────────────────────────────────────────────────────────────────────────────
# Base mode resolution
# ──────────────────────────────────────────────────────────────────────────────
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
    seen, out = set(), []
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
                if "base_modes" in data:  # new
                    return _parse_bases(data["base_modes"])
                if "base_mode" in data:   # legacy
                    return _parse_bases(data["base_mode"])
    except Exception:
        pass
    return None


def _save_choice(bases: list[str]) -> None:
    try:
        CONFIG_PATH.write_text(json.dumps({"base_modes": bases}, indent=2, sort_keys=True), encoding="utf-8")
        _log_info(f"[ProfOak] Saved base selection to {CONFIG_PATH.name}: {bases}")
    except Exception as e:
        _log_warn(f"[ProfOak] Could not save base selection: {e}")


def _try_import_candidates(canon: str):
    """Try built-ins first, then regular modes."""
    name_map = {"levelgrind": ("level_grind", "LevelGrind"), "spin": ("spin", "Spin")}
    mod_name, class_name = name_map.get(canon, (canon, canon.capitalize()))

    # built-in
    try:
        m = __import__(f"modules.built_in_modes.{mod_name}", fromlist=[class_name])
        cls = getattr(m, class_name, None)
        if cls: return cls
    except Exception:
        pass

    # regular
    try:
        m = __import__(f"modules.modes.{mod_name}", fromlist=[class_name])
        cls = getattr(m, class_name, None)
        if cls: return cls
    except Exception:
        pass
    return None


def _find_mode_in_registry(canon: str):
    try:
        from modules.modes import get_bot_modes  # type: ignore
        for cls in get_bot_modes():
            if cls is None: continue
            mode_name = None
            try:
                name_fn = getattr(cls, "name", None)
                mode_name = name_fn() if callable(name_fn) else None
            except Exception:
                mode_name = None
            cand = (_norm_name(str(mode_name)) if isinstance(mode_name, str) else None) or _norm_name(getattr(cls, "__name__", ""))
            if cand == canon: return cls
            if canon == "levelgrind" and cand in {"level_grind", "levelgrindmode"}: return cls
            if canon == "spin" and cand in {"spinner", "spinmode"}: return cls
    except Exception:
        pass
    return None


def _resolve_base_class(preferred: Sequence[str]) -> Tuple[Optional[type], Optional[str]]:
    for raw in preferred:
        canon = _norm_name(raw)
        cls = _try_import_candidates(canon) or _find_mode_in_registry(canon)
        if cls: return cls, raw
    return None, None


def _discover_available(basenames: list[str]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for raw in basenames:
        canon = _norm_name(raw)
        cls = _try_import_candidates(canon) or _find_mode_in_registry(canon)
        if cls: out[raw] = cls
    return out


def _get_preferred_bases() -> list[str]:
    return _load_saved_choice() or _parse_bases(os.getenv("PROFOAK_BASE") or "") or list(PLUGIN_DEFAULT_BASES)


def _maybe_prompt_once(defaults: list[str]) -> list[str]:
    if not ASK_ON_FIRST_USE or CONFIG_PATH.exists() or not sys.stdin or not sys.stdin.isatty():
        return defaults
    avail = _discover_available(["LevelGrind", "Spin"])
    if not avail: return defaults
    print("\n[ProfOak] Pick a base mode to wrap:")
    options = list(avail.keys())
    for i, name in enumerate(options, 1): print(f"  {i}) {name}")
    print(f"Press ENTER for default [{defaults[0]}].")
    try: choice = input("> ").strip()
    except Exception: choice = ""
    if choice.isdigit():
        idx = int(choice) - 1
        if 0 <= idx < len(options):
            chosen = options[idx]
            bases = [chosen] + [b for b in defaults if _norm_name(b) != _norm_name(chosen)]
            _save_choice(bases); return bases
    _save_choice(defaults); return defaults


# ──────────────────────────────────────────────────────────────────────────────
# ShinyQuota bridge (ensure instance is in the global plugin list)
# ──────────────────────────────────────────────────────────────────────────────
def _ensure_shiny_quota_loaded():
    """
    Import ShinyQuota from plugins/ProfOak/shiny_quota.py and ensure a live instance
    is present in modules.plugins.plugins (the bot's global plugin registry).
    """
    try:
        sq_mod = importlib.import_module("plugins.ProfOak.shiny_quota")
    except Exception as e:
        _log_warn(f"[ProfOak] Could not import ShinyQuota: {e}")
        return None

    SQ = getattr(sq_mod, "ShinyQuotaPlugin", None)
    if SQ is None:
        _log_warn("[ProfOak] ShinyQuotaPlugin class not found in plugins/ProfOak/shiny_quota.py")
        return None

    try:
        from modules.plugins import get_plugin_instance, is_plugin_loaded, plugins as registry  # type: ignore
        inst = get_plugin_instance(SQ)
        if inst:
            return inst
        # not present → create and append to global registry
        inst = SQ()
        registry.append(inst)
        _log_info("[ProfOak] Registered ShinyQuota in global plugin registry.")
        return inst
    except Exception as e:
        _log_warn(f"[ProfOak] Could not register ShinyQuota: {e}")
        # last-resort: keep a reference on context (not ideal, but better than nothing)
        try:
            lst = getattr(context, "plugins", None)
            if isinstance(lst, list):
                inst = SQ(); lst.append(inst); return inst
        except Exception:
            pass
    return None


def _configure_shiny_quota(living: bool) -> None:
    inst = _ensure_shiny_quota_loaded()
    if not inst:
        return

    # Preferred explicit setters (newer SQ)
    for setter in ("set_livingdex_enabled", "set_living_dex", "enable_living_dex", "enable_livingdex"):
        fn = getattr(inst, setter, None)
        if callable(fn):
            try: fn(bool(living)); break
            except Exception: pass
    else:
        # legacy style
        for setter in ("set_mode", "set_quota_mode"):
            fn = getattr(inst, setter, None)
            if callable(fn):
                try: fn("LIVING" if living else "STANDARD"); break
                except Exception: pass

    rf = getattr(inst, "force_refresh", None)
    if callable(rf):
        try: rf()
        except Exception: pass


# ──────────────────────────────────────────────────────────────────────────────
# Mode factory
# ──────────────────────────────────────────────────────────────────────────────
def _make_wrapped_mode(Base: Type["BotMode"], pretty: str, living: bool) -> Type["BotMode"]:
    mode_name = "Living Prof Oak" if living else "Prof Oak"

    class _Wrapped(Base):  # type: ignore[misc]
        @staticmethod
        def name() -> str:
            return mode_name

        @staticmethod
        def description() -> str:
            if living:
                return f"Living Prof Oak wrapper using {pretty}. Requires a shiny for each evo stage."
            return f"Prof Oak wrapper using {pretty}. Pause/navigate when route quota is met."

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)  # type: ignore
            _ensure_shiny_quota_loaded()
            _configure_shiny_quota(living)
            which = "Living-dex" if living else "Standard"
            _log_info(f"[{mode_name}] Initialized (base: {pretty}, ShinyQuota={which}).")

        def run(self):  # type: ignore[override]
            _log_info(f"[{mode_name}] Delegating run() to {pretty}.")
            yield from super().run()  # type: ignore

    return _Wrapped


# ──────────────────────────────────────────────────────────────────────────────
# Plugin: register both wrapped modes
# ──────────────────────────────────────────────────────────────────────────────
class ProfOakPlugin(BotPlugin):
    name = "ProfOakPlugin"
    version = __version__
    author = "HighVoltaage"
    description = (
        "Adds 'Prof Oak' and 'Living Prof Oak' modes that wrap a configurable base mode "
        "(e.g., LevelGrind or Spin). Living Prof Oak flips ShinyQuota's living-dex flag."
    )

    def get_additional_bot_modes(self) -> Iterable[type["BotMode"]]:
        _ensure_shiny_quota_loaded()  # make sure SQ exists before modes start

        preferred = _maybe_prompt_once(_get_preferred_bases())
        Base, chosen = _resolve_base_class(preferred)
        if Base is None:
            _log_warn(f"[ProfOak] Could not resolve any base from: {preferred}. Not registering modes.")
            return []

        pretty = chosen or getattr(Base, "__name__", "UnknownBase")
        _log_info(f"[ProfOak] Registering Prof Oak modes (base: {pretty}).")

        ProfOakMode = _make_wrapped_mode(Base, pretty, living=False)
        LivingProfOakMode = _make_wrapped_mode(Base, pretty, living=True)
        return [ProfOakMode, LivingProfOakMode]

from plugins.ProfOak import navigator as prof_oak_navigator
from modules.plugin_interface import BotPlugin

class ProfOakNavigatorBridge(BotPlugin):
    name = "ProfOakNavigatorBridge"
    version = "0.1.1"
    description = "Registers the Prof Oak Navigator listener."
    author = "HighVoltaage"

    def get_additional_bot_listeners(self):
        return [prof_oak_navigator.get_listener()]
