"""In-memory state graph built from the store's transitions."""
from __future__ import annotations

from collections import defaultdict

from .models import StateTransition
from .store import StateStore


class StateGraph:
    """Directed multigraph: nodes are state names, edges are transitions
    (typed by role + action). Multiple edges between the same pair are
    allowed (e.g., different ways to get from A to B)."""

    def __init__(self, app: str, version: str,
                 transitions: list[StateTransition]):
        self.app = app
        self.version = version
        self._out: dict[str, list[StateTransition]] = defaultdict(list)
        for t in transitions:
            self._out[t.from_state].append(t)

    @classmethod
    def load(cls, store: StateStore, app: str, version: str) -> "StateGraph":
        trans = store.transitions_for(app, version)
        if not trans:
            # Fallback: borrow transitions from other versions as priors.
            # Many apps keep their nav structure stable across minor releases.
            for older in _other_versions(store, app, version):
                t = store.transitions_for(app, older)
                if t:
                    trans = t
                    break
        return cls(app, version, trans)

    # ------- queries -------
    def edges_from(self, state: str) -> list[StateTransition]:
        return list(self._out.get(state, []))

    def nodes(self) -> set[str]:
        nodes = set(self._out.keys())
        for edges in self._out.values():
            for e in edges:
                nodes.add(e.to_state)
        return nodes


def _other_versions(store: StateStore, app: str,
                    current_version: str) -> list[str]:
    rows = store._con.execute(
        "SELECT DISTINCT app_version FROM state_transitions "
        "WHERE app_package=? AND app_version<>? "
        "ORDER BY app_version DESC",
        (app, current_version),
    ).fetchall()
    return [r["app_version"] for r in rows]
