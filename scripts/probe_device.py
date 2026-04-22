"""Smoke-test device connectivity and perception without touching Ollama.

Run:
    python scripts/probe_device.py
"""
from __future__ import annotations

import json

from chimera.core.driver import Driver
from chimera.execution.executor import Executor  # noqa: F401 (just ensures import graph)
from chimera.perception.capture import capture
from chimera.perception.fingerprint import screen_fingerprint
from chimera.perception.parser import parse


def main():
    d = Driver.connect()
    info = d.device_info()
    print("device:", info.get("brand"), info.get("model"), info.get("version"))
    snap = capture(d, with_screenshot=False)
    root, flat = parse(snap["xml"])
    fp = screen_fingerprint(flat, snap["package"])
    print(f"package={snap['package']} activity={snap['activity']} fp={fp} nodes={len(flat)}")
    # Top 5 interactive nodes
    interactive = [n for n in flat if n.clickable][:5]
    for n in interactive:
        print(json.dumps(n.to_slim()))


if __name__ == "__main__":
    main()
