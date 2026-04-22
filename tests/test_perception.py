from pathlib import Path

from chimera.perception.parser import parse
from chimera.perception.fingerprint import screen_fingerprint

FIX = Path(__file__).parent / "fixtures" / "whatsapp_chat.xml"


def test_parse_flattens_all_nodes():
    xml = FIX.read_text()
    root, flat = parse(xml)
    assert root.cls == "android.widget.FrameLayout"
    # root + linearlayout + toolbar + 2 toolbar children + framelayout + 2 input children
    assert len(flat) == 8
    send = next(n for n in flat if n.resource_id == "com.whatsapp:id/send")
    assert send.content_desc == "Send"
    assert send.clickable is True
    assert send.center == ((864 + 1056) // 2, (2232 + 2368) // 2)


def test_fingerprint_is_stable_and_app_sensitive():
    xml = FIX.read_text()
    _, flat = parse(xml)
    fp1 = screen_fingerprint(flat, "com.whatsapp")
    fp2 = screen_fingerprint(flat, "com.whatsapp")
    fp3 = screen_fingerprint(flat, "com.telegram.messenger")
    assert fp1 == fp2
    assert fp1 != fp3
    assert len(fp1) == 16
