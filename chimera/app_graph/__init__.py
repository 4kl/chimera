"""Declarative, PUMA-style state graph on top of Chimera's self-healing core.

The happy path is 100% XPath-driven (zero LLM calls). If an XPath stops
resolving (app update, element renamed), the hybrid driver falls back to
Chimera's LLM-backed discovery, learns the repaired selector, and caches it
under the current app version so the next run is XPath-fast again."""
from .declared_state import DeclaredState, xpath_to_features
from .compose import compose_clicks
from .actions import action
from .app_graph import AppGraph, supported_version
from .xpath_driver import XPathDriver, XPathNotFound
from .popup import PopupHandler, simple_popup_handler

__all__ = [
    "AppGraph", "DeclaredState", "XPathDriver", "XPathNotFound",
    "action", "compose_clicks", "supported_version",
    "PopupHandler", "simple_popup_handler", "xpath_to_features",
]
