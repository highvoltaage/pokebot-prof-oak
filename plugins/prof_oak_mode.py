# -*- coding: utf-8 -*-
# plugins/prof_oak_mode.py
#
# Adds a new bot mode "Prof Oak" that wraps the existing Level Grind mode.
# It finds Level Grind dynamically from the bot's mode registry, so it works
# across forks/paths. ShinyQuota keeps handling quota/pausing.

from typing import Iterable, Generator, TYPE_CHECKING

from modules.plugin_interface import BotPlugin
from modules.context import context

if TYPE_CHECKING:
    from modules.modes import BotMode  # typing only

# -------- logging helpers --------
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

# -------- resolve Level Grind base class --------
def _find_level_grind_base():
    # 1) Known import paths on some forks
    try:
        from modules.built_in_modes.level_grind import LevelGrind as _Base  # type: ignore
        return _Base
    except Exception:
        pass
    try:
        from modules.modes.level_grind import LevelGrind as _Base  # type: ignore
        return _Base
    except Exception:
        pass

    # 2) Dynamic discovery via the bot's registry
    try:
        from modules.modes import get_bot_modes  # type: ignore
        for cls in get_bot_modes():
            name_fn = getattr(cls, "name", None)
            cls_name = getattr(cls, "__name__", "").lower()
            try:
                mode_name = name_fn() if callable(name_fn) else None
            except Exception:
                mode_name = None

            if (
                (isinstance(mode_name, str) and mode_name.strip().lower() in {"level grind", "level-grind", "level_grind"})
                or cls_name in {"levelgrind", "level_grind", "levelgrindmode"}
            ):
                return cls
    except Exception:
        pass

    return None

# -------- plugin that registers our mode --------
class ProfOakPlugin(BotPlugin):
    name = "ProfOakPlugin"
    version = "0.2.0-alpha.1"
    author = "HighVoltaage"
    description = "Adds the 'Prof Oak' mode (wrapper around Level Grind)."

    def get_additional_bot_modes(self) -> Iterable[type["BotMode"]]:
        Base = _find_level_grind_base()
        if Base is None:
            _log_warn("[ProfOak] Could not locate Level Grind; not registering Prof Oak.")
            return []

        # Define the subclass *now* using the resolved base.
        class ProfOakMode(Base):  # type: ignore
            @staticmethod
            def name() -> str:
                return "Prof Oak"

            @staticmethod
            def description() -> str:
                return ("Prof Oak challenge wrapper. Runs Level Grind behavior while "
                        "the ShinyQuota plugin tracks per-map/method shiny quotas and pauses when complete.")

            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)  # type: ignore
                _log_info("[ProfOak] Mode initialized (wrapping Level Grind base: %s)." % Base.__name__)

            def run(self) -> "Generator":  # type: ignore[override]
                _log_info("[ProfOak] Starting run loop (delegating to Level Grind logic).")
                yield from super().run()  # type: ignore

        _log_info("[ProfOak] Mode registered (base: %s)." % Base.__name__)
        return [ProfOakMode]
