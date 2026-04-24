"""The @action(state) decorator.

Wraps a method on an AppGraph subclass so that, when called:
  1. The current state is detected.
  2. If it doesn't match the declared state, the navigator is invoked to
     move there (using transitions declared via `state.to(target, via=...)`).
  3. If the declared state has a validator and the caller passed context
     kwargs (e.g. `conversation="John"`), the validator runs; if it rejects,
     we explicitly navigate into the right context.
  4. The wrapped method runs.
  5. If `end_state` was declared, we verify we landed there afterward (and
     record the transition so the graph learns)."""
from __future__ import annotations

import functools
import logging
from typing import Callable, Optional

from .declared_state import DeclaredState

log = logging.getLogger("chimera.app_graph")


def action(state: DeclaredState, *, end_state: Optional[DeclaredState] = None):
    """Decorator: bind `fn` to `state` as the valid calling context."""
    def decorate(fn: Callable) -> Callable:
        @functools.wraps(fn)
        def wrapper(self, *args, **kwargs):
            # `self` is an AppGraph instance
            self._enter_state(state, context_kwargs=kwargs)
            out = fn(self, *args, **kwargs)
            if end_state is not None:
                self._expect_state(end_state, previous=state)
            return out
        wrapper._action_state = state
        wrapper._action_end_state = end_state
        return wrapper
    return decorate
