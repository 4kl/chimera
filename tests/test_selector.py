from pathlib import Path

from chimera.perception.parser import parse
from chimera.selector.generator import SelectorGenerator

FIX = Path(__file__).parent / "fixtures" / "whatsapp_chat.xml"


def _by_rid(flat, rid):
    return next(n for n in flat if n.resource_id == rid)


def test_primary_for_unique_resource_id():
    _, flat = parse(FIX.read_text())
    node = _by_rid(flat, "com.whatsapp:id/send")
    cands = SelectorGenerator().generate(node, flat)
    # top candidate should be resource-id based
    assert cands[0].strategy == "resource_id"
    assert "resource-id=" in cands[0].expr
    assert "com.whatsapp:id/send" in cands[0].expr
    # content-desc present — should appear in candidate list
    strategies = {c.strategy for c in cands}
    assert "content_desc" in strategies


def test_generator_falls_back_when_no_rid():
    _, flat = parse(FIX.read_text())
    nav_up = next(n for n in flat if n.content_desc == "Navigate up")
    cands = SelectorGenerator().generate(nav_up, flat)
    # top candidate should be content-desc
    assert cands[0].strategy == "content_desc"
    # must still produce a last-resort xpath
    assert any(c.strategy == "abs_xpath" for c in cands)


def test_dynamic_text_is_penalized():
    _, flat = parse(FIX.read_text())
    # simulate a dynamic text node by mutating the contact name
    contact = _by_rid(flat, "com.whatsapp:id/conversation_contact_name")
    contact.text = "5 new messages"  # dynamic-looking
    cands = SelectorGenerator().generate(contact, flat)
    text_cands = [c for c in cands if c.strategy.startswith("text")]
    for c in text_cands:
        assert c.score <= 0.55  # 0.70 base - 0.25 dyn penalty
