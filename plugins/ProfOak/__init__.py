# plugins/ProfOak/__init__.py

# Re-export the submodule explicitly so "from plugins.ProfOak import navigator"
# always yields the module object, regardless of Python’s import caching rules.
from . import navigator as navigator

# (Optional) also re-export the functions if you ever want
#   from plugins.ProfOak import on_quota_met, get_listener
from .navigator import on_quota_met, get_listener

__all__ = ["navigator", "on_quota_met", "get_listener"]
