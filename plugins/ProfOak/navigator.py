# plugins/ProfOak/navigator.py
# Prof Oak Navigator — skeleton that logs state changes verbosely.
# No real movement yet; this just shows the lifecycle is wired up.

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional

from modules.context import context

# ---------------------------
# Debug controls
# ---------------------------
DEBUG_VERBOSE: bool = True           # log every state change and handoff details
HEARTBEAT_EVERY: int = 60            # frames between "still traveling..." messages

# ---------------------------
# States
# ---------------------------
STATE_IDLE = "IDLE"
STATE_TRAVELING = "TRAVELING"
STATE_ESCAPING = "ESCAPING_ENCOUNTER"
STATE_RESUMING = "RESUMING"
STATE_ARRIVED = "ARRIVED"

# ---------------------------
# Target data
# ---------------------------
@dataclass
class Target:
    current_map: str
    method: str
    needs: Dict[str, int]          # species -> how many still needed
    livingdex: bool


# ---------------------------
# Logging helpers
# ---------------------------
def _info(msg: str) -> None:
    lg = getattr(context, "log", None) or getattr(context, "logger", None)
    if lg and hasattr(lg, "info"):
        try:
            lg.info(msg); return
        except Exception:
            pass
    print(msg)

def _warn(msg: str) -> None:
    lg = getattr(context, "log", None) or getattr(context, "logger", None)
    if lg and hasattr(lg, "warning"):
        try:
            lg.warning(msg); return
        except Exception:
            pass
    print(f"WARNING: {msg}")

def _status(msg: str) -> None:
    try:
        if getattr(context, "overlay", None):
            context.overlay.set_status_line(msg)
    except Exception:
        pass


# ---------------------------
# Frame listener (state machine)
# ---------------------------
class ProfOakNavigatorListener:
    """
    Minimal BotListener: implement handle_frame(*args, **kwargs) to be compatible with
    engines that pass (mode, frame_info) or nothing.
    """

    def __init__(self) -> None:
        self.state: str = STATE_IDLE
        self.target: Optional[Target] = None
        self.frame_counter: int = 0
        self._registered_once: bool = False  # set in get_listener()

        if DEBUG_VERBOSE:
            _info("[Navigator] Listener constructed; initial state=IDLE")

    # ---- internal: state setter that logs transitions ---------------
    def _set_state(self, new_state: str, reason: str = "") -> None:
        old = self.state
        self.state = new_state
        if DEBUG_VERBOSE:
            if reason:
                _info(f"[Navigator] {old} -> {new_state}  ({reason})")
            else:
                _info(f"[Navigator] {old} -> {new_state}")
        _status(f"[Navigator] {new_state}")

    # ---- public control helpers ------------------------------------
    def is_idle(self) -> bool:
        return self.state == STATE_IDLE

    def accept_handoff(self, target: Target) -> bool:
        """Receive control from ShinyQuota (quota met). Returns True if accepted."""
        if not self.is_idle():
            _warn("[Navigator] Busy; declining handoff.")
            return False

        self.target = target
        self.frame_counter = 0

        # Pretty-print needs list (bounded to avoid giant lines)
        try:
            pairs = [f"{k}×{v}" for k, v in sorted(target.needs.items())]
            needs_str = ", ".join(pairs[:6]) + ("…" if len(pairs) > 6 else "")
        except Exception:
            needs_str = f"{len(target.needs)} species"

        if DEBUG_VERBOSE:
            _info(
                f"[Navigator] Accepted handoff: map={target.current_map}, "
                f"method={target.method}, livingdex={target.livingdex}; needs: {needs_str}"
            )

        self._set_state(STATE_TRAVELING, reason="quota met; begin skeleton travel")
        return True

    def cancel(self) -> None:
        self.target = None
        self.frame_counter = 0
        self._set_state(STATE_IDLE, reason="reset/cancel")

    # ---- BotListener hook: called each frame -----------------------
    def handle_frame(self, *args, **kwargs) -> None:
        """
        Accept arbitrary args to be compatible with different engine signatures:
        some call handle_frame(), others call handle_frame(mode, frame_info).
        """
        st = self.state

        if st == STATE_IDLE:
            return

        if st == STATE_TRAVELING:
            # SKELETON: no pathing yet — just heartbeat and "arrive" after ~3s at 60fps.
            self.frame_counter += 1
            if DEBUG_VERBOSE and self.frame_counter % HEARTBEAT_EVERY == 0:
                _info(f"[Navigator] TRAVELING… (t={self.frame_counter} frames)")
                _status("[Navigator] TRAVELING…")

            if self.frame_counter >= 180:
                self._set_state(STATE_ARRIVED, reason="simulated arrival (skeleton)")
            return

        if st == STATE_ARRIVED:
            _info("[Navigator] ARRIVED — handing back to base mode (skeleton).")
            self.cancel()
            return

        # Future states (will be fleshed out when we add movement):
        if st == STATE_ESCAPING:
            # TODO: run/handle encounter, then _set_state(STATE_RESUMING, "escaped")
            return

        if st == STATE_RESUMING:
            # TODO: resume overworld navigation
            return


# Global singleton so everyone shares the same navigator
_NAV = ProfOakNavigatorListener()

# ---------------------------
# API used by ShinyQuota
# ---------------------------
def on_quota_met(current_map: str, method: str, needs: Dict[str, int], livingdex: bool) -> bool:
    """
    Called by ShinyQuota when the route quota is met and ON_QUOTA='navigate'.
    Returns True iff the navigator accepted the handoff.
    """
    tgt = Target(current_map=current_map, method=method, needs=needs, livingdex=livingdex)
    return _NAV.accept_handoff(tgt)

# ---------------------------
# Plugin-bridge helper
# ---------------------------
def get_listener() -> ProfOakNavigatorListener:
    """Used by the bridge plugin to register our listener with the engine."""
    if DEBUG_VERBOSE and not _NAV._registered_once:
        _NAV._registered_once = True
        _info("[Navigator] Listener registered with engine (will receive frame ticks).")
    return _NAV
