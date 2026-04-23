from __future__ import annotations

import json
from typing import Any, Optional

from ..core.errors import ReasoningError
from ..core.models import ActionStep, Frame, Intent, UINode
from .ollama_client import Ollama
from .prompts import CLASSIFY_STATE_SYS, INTENT_SYS, MATCH_SYS


def llm_plan(ollama: Ollama, nl_command: str,
             known_states: Optional[list[str]] = None) -> Intent:
    """Decompose a natural-language command into an Intent."""
    user_payload: Any
    if known_states:
        user_payload = json.dumps({"command": nl_command,
                                   "known_states": known_states},
                                  ensure_ascii=False)
    else:
        user_payload = nl_command
    out = ollama.chat_json(INTENT_SYS, user_payload)
    steps_raw = out.get("steps") or []
    steps = [ActionStep(
        role=s.get("role", ""),
        action=s.get("action", "tap"),
        value=s.get("value"),
        description=s.get("description", ""),
        target_state=s.get("target_state"),
    ) for s in steps_raw if s.get("role")]
    return Intent(raw=nl_command, app_hint=out.get("app_hint"), steps=steps)


def llm_classify_state(ollama: Ollama, frame: Frame,
                       known_states: list[str]) -> dict[str, Any]:
    """Ask the LLM to classify the current screen into a semantic state."""
    elements = [n.to_slim() for n in frame.flat
                if n.resource_id or n.content_desc or n.text or n.clickable]
    user = json.dumps(
        {"known_states": known_states,
         "package": frame.package,
         "elements": elements[:120]},   # cap to keep prompt small
        ensure_ascii=False,
    )
    out = ollama.chat_json(CLASSIFY_STATE_SYS, user)
    if not isinstance(out, dict) or "state" not in out:
        raise ReasoningError(f"state classification missing 'state': {out}")
    return out


def _candidate_nodes(flat: list[UINode]) -> list[UINode]:
    """Prune to nodes a user could plausibly interact with or that carry
    identity. Keeps the LLM prompt small and unambiguous."""
    out = []
    for n in flat:
        has_identity = bool(n.resource_id or n.content_desc or n.text)
        if n.clickable or n.focused or has_identity:
            if n.bounds != (0, 0, 0, 0):
                out.append(n)
    return out


def llm_pick(ollama: Ollama, role: str, description: str,
             flat: list[UINode]) -> dict[str, Any]:
    cands = _candidate_nodes(flat)
    slim = [n.to_slim() for n in cands]
    user_payload = {
        "role": role,
        "description": description,
        "elements": slim,
    }
    out = ollama.chat_json(MATCH_SYS, json.dumps(user_payload, ensure_ascii=False))
    if "index" not in out:
        raise ReasoningError(f"LLM pick missing 'index': {out}")
    # Map LLM-visible indices (which match UINode.index directly) back
    return {
        "index": int(out["index"]),
        "confidence": float(out.get("confidence", 0.0)),
        "reason": out.get("reason", ""),
        "backup_indices": [int(i) for i in out.get("backup_indices", [])],
    }
