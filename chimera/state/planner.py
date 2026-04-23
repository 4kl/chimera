"""Dijkstra-style shortest-path planner over the state graph.

Edge cost = 1 + (1 - confidence) * 3 + 2 * (action == 'back' ? 1 : 0)^0
That is: low-confidence transitions cost more; plain `back` actions are
slightly penalized so the planner prefers forward paths when both exist.

A path is a list of StateTransition edges; applying each edge (by performing
its (role, action)) moves the UI from that edge's from_state to to_state."""
from __future__ import annotations

import heapq
import math
from typing import Optional

from .graph import StateGraph
from .models import StateTransition


MAX_PATH_LEN = 8   # safety cap on path length
BASE_EDGE_COST = 1.0
CONF_WEIGHT = 3.0


def _edge_cost(e: StateTransition) -> float:
    cost = BASE_EDGE_COST + (1.0 - e.confidence) * CONF_WEIGHT
    if e.action == "back":
        cost += 0.25   # prefer forward transitions if equally confident
    return cost


class StatePlanner:
    def __init__(self, graph: StateGraph):
        self.graph = graph

    def plan(self, start: str, goal: str) -> Optional[list[StateTransition]]:
        """Dijkstra from start to goal; returns an ordered list of
        transitions, or None if unreachable or path exceeds MAX_PATH_LEN."""
        if start == goal:
            return []

        dist: dict[str, float] = {start: 0.0}
        prev: dict[str, tuple[str, StateTransition] | None] = {start: None}
        pq: list[tuple[float, int, str]] = [(0.0, 0, start)]
        seq = 0

        while pq:
            d, _, u = heapq.heappop(pq)
            if u == goal:
                return self._reconstruct(prev, goal)
            if d > dist.get(u, math.inf):
                continue
            for e in self.graph.edges_from(u):
                v = e.to_state
                nd = d + _edge_cost(e)
                if nd < dist.get(v, math.inf):
                    dist[v] = nd
                    prev[v] = (u, e)
                    seq += 1
                    heapq.heappush(pq, (nd, seq, v))

        return None

    def _reconstruct(self, prev, goal) -> list[StateTransition]:
        path: list[StateTransition] = []
        node = goal
        while prev.get(node) is not None:
            u, e = prev[node]
            path.append(e)
            node = u
        path.reverse()
        if len(path) > MAX_PATH_LEN:
            return path[:MAX_PATH_LEN]
        return path
