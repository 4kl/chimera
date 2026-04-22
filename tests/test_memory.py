from chimera.core.models import SelectorBundle, SelectorCandidate
from chimera.memory.store import Memory


def test_memory_roundtrip(tmp_path):
    db = tmp_path / "chim.db"
    mem = Memory(str(db))

    bundle = SelectorBundle(
        primary=SelectorCandidate("//*[@resource-id='x']", "resource_id", 0.95),
        fallbacks=[SelectorCandidate("//*[@content-desc='Send']", "content_desc", 0.85)],
        semantic_role="send_button",
        app_package="com.whatsapp",
        app_version="2.24.10",
        screen_fingerprint="abc123",
        element_fingerprint="fp-send",
        description="blue paper-plane icon, bottom-right",
    )
    mem.put(bundle)
    got = mem.get("com.whatsapp", "2.24.10", "abc123", "send_button")
    assert got is not None
    assert got.primary.expr == bundle.primary.expr
    assert len(got.fallbacks) == 1
    assert got.fallbacks[0].strategy == "content_desc"
    assert got.description.startswith("blue paper-plane")

    # upsert + bundle-revision bump
    bundle.primary = SelectorCandidate("//*[@content-desc='Send']", "content_desc", 0.85)
    bundle.version = 2
    mem.put(bundle)
    got2 = mem.get("com.whatsapp", "2.24.10", "abc123", "send_button")
    assert got2.version == 2
    assert got2.primary.strategy == "content_desc"

    mem.log("com.whatsapp", "2.24.10", "send_button", "healed", bundle.primary.expr)
    mem.close()
