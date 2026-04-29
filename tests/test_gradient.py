from ch.gradient import banner, gradient_text, hex_to_rgb, lerp, rgb_to_hex


def test_hex_to_rgb_roundtrip():
    assert hex_to_rgb("#ff5bc0") == (0xff, 0x5b, 0xc0)
    assert rgb_to_hex(0xff, 0x5b, 0xc0) == "#ff5bc0"


def test_lerp():
    assert lerp(0, 10, 0.0) == 0
    assert lerp(0, 10, 1.0) == 10
    assert lerp(0, 10, 0.5) == 5


def test_gradient_text_two_stops():
    t = gradient_text("hello", "#ff0000", "#0000ff")
    spans = t.spans
    # Five characters → at least one span per character
    assert len(spans) >= 5


def test_gradient_text_single_stop():
    t = gradient_text("solid", "#ff0000")
    # Single stop → uniform style
    assert "ff0000" in str(t.style).lower() or any("ff0000" in str(s.style).lower() for s in t.spans)


def test_gradient_multistop():
    t = gradient_text("longerstring", "#ff0000", "#00ff00", "#0000ff")
    # Should produce per-character distinct colors across 12 chars
    assert len(t.spans) >= 12


def test_banner_returns_text():
    b = banner("ch", palette="charm")
    assert b.plain == "ch"
