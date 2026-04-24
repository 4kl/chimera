"""Popup handlers — register dialogs that can appear at any time so the
AppGraph can dismiss them transparently before the user's action runs."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


@dataclass
class PopupHandler:
    """A popup is identified by any of `identifiers` being present on screen.
    When detected, each XPath in `dismiss_sequence` is clicked in order."""
    identifiers: list[str]
    dismiss_sequence: list[str]

    def matches(self, driver) -> bool:
        return any(driver.is_present(x, timeout=0.5) for x in self.identifiers)

    def dismiss(self, driver):
        for x in self.dismiss_sequence:
            try:
                driver.click(x, timeout=1.0)
            except Exception:
                pass  # best-effort


def simple_popup_handler(dismiss_xpath: str) -> PopupHandler:
    """Shortcut: a popup identified by and dismissed via the same XPath
    (e.g. an "OK" button whose presence signals the dialog)."""
    return PopupHandler(identifiers=[dismiss_xpath],
                        dismiss_sequence=[dismiss_xpath])
