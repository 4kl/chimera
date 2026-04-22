import time

from chimera.core.models import SelectorBundle, SelectorCandidate
from chimera.memory.store import Memory, decayed_score


def _make(app="com.whatsapp", version="2.24.10", screen="scrA",
          role="send_button", expr="//*[@resource-id='x']",
          score=0.95):
    return SelectorBundle(
        primary=SelectorCandidate(expr, "resource_id", score, "learned"),
        fallbacks=[SelectorCandidate("//*[@content-desc='Send']",
                                     "content_desc", 0.85, "learned")],
        semantic_role=role,
        app_package=app,
        app_version=version,
        screen_fingerprint=screen,
        element_fingerprint="fp-xy",
        description="send icon bottom-right",
    )


def test_roundtrip_with_version(tmp_path):
    m = Memory(str(tmp_path / "v.db"))
    b = _make()
    m.put(b)

    got = m.get("com.whatsapp", "2.24.10", "scrA", "send_button")
    assert got is not None
    assert got.app_version == "2.24.10"
    assert got.primary.provenance == "learned"

    # different app → absolutely no hit (no cross-app sharing allowed)
    assert m.get("com.other", "2.24.10", "scrA", "send_button") is None


def test_wildcard_screen_fallback(tmp_path):
    m = Memory(str(tmp_path / "w.db"))
    b = _make(screen="*")
    m.put(b)
    # lookup with specific screen should fall back to '*'
    got = m.get("com.whatsapp", "2.24.10", "scrSpecific", "send_button")
    assert got is not None
    assert got.screen_fingerprint == "*"


def test_version_similarity_and_migration(tmp_path):
    m = Memory(str(tmp_path / "m.db"))
    # v1: seen on screens A, B
    m.put(_make(version="1.0", screen="scrA"))
    m.put(_make(version="1.0", screen="scrB", role="search_bar",
                expr="//*[@resource-id='search']"))
    m.record_screen("com.whatsapp", "1.0", "scrA")
    m.record_screen("com.whatsapp", "1.0", "scrB")

    # v2: seen on screens A, B too (high jaccard)
    m.record_screen("com.whatsapp", "2.0", "scrA")
    m.record_screen("com.whatsapp", "2.0", "scrB")

    assert m.version_similarity("com.whatsapp", "1.0", "2.0") == 1.0
    prior = m.find_similar_version("com.whatsapp", "2.0")
    assert prior == "1.0"

    copied = m.migrate_from("com.whatsapp", "1.0", "2.0", jaccard=1.0)
    assert copied == 2

    got = m.get("com.whatsapp", "2.0", "scrA", "send_button")
    assert got is not None
    assert got.primary.provenance == "migrated"
    # Score should be scaled down vs. the original (0.95 * 0.8 = 0.76)
    assert got.primary.score == 0.95 * 0.8


def test_get_cross_version_seed_without_explicit_migration(tmp_path):
    m = Memory(str(tmp_path / "seed.db"))
    # Stored under an old version; no migration ever run.
    m.put(_make(version="1.0", screen="scrA"))
    # Lookup under a new version should return a migrated seed.
    got = m.get("com.whatsapp", "2.0", "scrA", "send_button")
    assert got is not None
    assert got.app_version == "2.0"       # re-parented to requested version
    assert got.primary.provenance == "migrated"


def test_bump_failure_persists(tmp_path):
    m = Memory(str(tmp_path / "f.db"))
    b = _make()
    m.put(b)
    got = m.get("com.whatsapp", "2.24.10", "scrA", "send_button")
    m.bump_failure(got)
    m.bump_failure(got)
    again = m.get("com.whatsapp", "2.24.10", "scrA", "send_button")
    assert again.failures == 2


def test_cross_screen_fallback(tmp_path):
    """Same (app, version), different screen_fp → get() returns the latest
    bundle with _origin_screen_fp set so the learning engine can REVALIDATE."""
    m = Memory(str(tmp_path / "xs.db"))
    m.put(_make(screen="scrA"))
    # Lookup on a different screen fp — should return the scrA bundle with
    # origin marker set.
    got = m.get("com.whatsapp", "2.24.10", "scrB", "send_button")
    assert got is not None
    assert got.screen_fingerprint == "scrA"
    assert got._origin_screen_fp == "scrA"
    # Different app → still isolated.
    assert m.get("com.other", "2.24.10", "scrB", "send_button") is None


def test_cross_screen_does_not_override_exact(tmp_path):
    m = Memory(str(tmp_path / "xsoverride.db"))
    # Two bundles: one on scrA, one on scrB, same role/version
    m.put(_make(screen="scrA", expr="//*[@resource-id='A']"))
    m.put(_make(screen="scrB", expr="//*[@resource-id='B']"))
    # Exact hit for scrB returns B, not A
    got = m.get("com.whatsapp", "2.24.10", "scrB", "send_button")
    assert got is not None
    assert got.primary.expr == "//*[@resource-id='B']"
    assert got._origin_screen_fp is None  # exact hit, no cross-screen marker


def test_decay_formula_bounds():
    now = 1_000_000_000.0
    # Fresh, no failures → full score.
    assert decayed_score(0.9, now, 0, now=now) == 0.9
    # Many failures → capped at 0.45 decay.
    low = decayed_score(0.9, now, 10, now=now)
    assert low == 0.9 * (1.0 - 0.45)  # age decay = 0 because last_ok == now
    # Very old → age decay caps at 0.4.
    old = decayed_score(0.9, now - 86400 * 365, 0, now=now)
    assert old == 0.9 * (1.0 - 0.4)
    # Never below 0.1.
    assert decayed_score(0.2, now - 86400 * 365, 10, now=now) == 0.1
