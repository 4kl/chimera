from __future__ import annotations

import json
import os
from typing import Any, Optional

import httpx

from ..core.errors import ReasoningError


class Ollama:
    def __init__(self,
                 url: Optional[str] = None,
                 model: Optional[str] = None,
                 timeout: float = 60.0):
        self.url = url or os.environ.get("OLLAMA_URL", "http://localhost:11434")
        self.model = model or os.environ.get("OLLAMA_MODEL", "qwen2.5:7b-instruct")
        self._timeout = timeout

    def chat_json(self, system: str, user: str) -> dict[str, Any]:
        """Call Ollama's /api/chat with `format: json` and parse the response."""
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "stream": False,
            "format": "json",
            "options": {"temperature": 0.1, "top_p": 0.9},
        }
        try:
            r = httpx.post(f"{self.url}/api/chat", json=payload, timeout=self._timeout)
            r.raise_for_status()
        except httpx.HTTPError as e:
            raise ReasoningError(f"Ollama request failed: {e}") from e
        body = r.json()
        content = body.get("message", {}).get("content", "")
        if not content:
            raise ReasoningError(f"Empty Ollama response: {body}")
        try:
            return json.loads(content)
        except json.JSONDecodeError as e:
            raise ReasoningError(f"Ollama returned non-JSON content: {content!r}") from e
