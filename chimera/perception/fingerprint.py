from __future__ import annotations

import hashlib

from ..core.models import UINode


def screen_fingerprint(flat: list[UINode], package: str) -> str:
    """Stable identity of a screen, invariant to dynamic content.

    Uses (package, set-of-resource-ids, set-of-class-names). That's enough to
    separate e.g. WhatsApp chat-list vs chat-thread vs contact-picker, while
    ignoring things like unread-count text or timestamps.
    """
    rids = sorted({n.resource_id for n in flat if n.resource_id})
    classes = sorted({n.cls for n in flat})
    raw = f"{package}::{','.join(classes)}::{','.join(rids)}"
    return hashlib.sha1(raw.encode()).hexdigest()[:16]
