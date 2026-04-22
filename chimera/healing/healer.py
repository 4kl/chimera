from __future__ import annotations

from ..core.errors import HealError
from ..core.models import Frame, SelectorBundle
from ..memory.store import Memory
from ..reasoning.matcher import llm_pick
from ..reasoning.ollama_client import Ollama
from ..selector.generator import SelectorGenerator


class Healer:
    """Re-binds a stale SelectorBundle to a node on the current screen using
    the LLM (primary) and, when available, visual similarity (fallback)."""

    def __init__(self, min_confidence: float = 0.5):
        self.min_confidence = min_confidence

    def heal(self, bundle: SelectorBundle, frame: Frame, llm: Ollama,
             gen: SelectorGenerator, mem: Memory,
             description: str | None = None) -> SelectorBundle:
        desc = description or mem.description_for(bundle)

        pick = llm_pick(llm, bundle.semantic_role, desc, frame.flat)
        provenance = "healed"

        if pick["confidence"] < self.min_confidence or pick["index"] < 0:
            visual = _try_visual(bundle, frame)
            if visual is not None:
                pick = visual
                provenance = "healed-visual"

        if pick["index"] < 0 or pick["index"] >= len(frame.flat):
            raise HealError(
                f"healer could not locate role={bundle.semantic_role!r}: "
                f"{pick.get('reason', '')}")

        node = frame.flat[pick["index"]]
        cands = gen.generate(node, frame.flat)
        for c in cands:
            c.provenance = provenance

        healed = SelectorBundle(
            primary=cands[0],
            fallbacks=cands[1:4],
            semantic_role=bundle.semantic_role,
            app_package=bundle.app_package,
            app_version=bundle.app_version,
            screen_fingerprint=frame.fp,
            element_fingerprint=node.semantic_key(),
            description=desc,
            version=bundle.version + 1,
        )
        mem.put(healed)
        mem.log(bundle.app_package, bundle.app_version,
                bundle.semantic_role, "healed",
                healed.primary.expr, note=provenance)
        return healed


def _try_visual(bundle: SelectorBundle, frame: Frame):
    """Placeholder for visual healing. Requires opencv-python + reference
    crops; returns None if not configured. Kept as a seam."""
    try:
        import cv2  # noqa: F401
    except Exception:
        return None
    # A real implementation would:
    #   - load a saved crop for bundle.element_fingerprint
    #   - crop each candidate node from frame.png
    #   - compute template match / CLIP similarity
    #   - return the argmax with a confidence score
    return None
