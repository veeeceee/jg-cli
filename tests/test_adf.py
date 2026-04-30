from jg.adf import render_to_text, sections_to_adf, text_to_adf


def test_text_to_adf_paragraph():
    doc = text_to_adf("Hello world")
    assert doc["type"] == "doc"
    assert doc["content"][0]["type"] == "paragraph"
    assert doc["content"][0]["content"][0]["text"] == "Hello world"


def test_text_to_adf_bullets():
    doc = text_to_adf("- one\n- two\n- three")
    assert doc["content"][0]["type"] == "bulletList"
    assert len(doc["content"][0]["content"]) == 3


def test_text_to_adf_paragraph_then_bullets():
    doc = text_to_adf("Intro paragraph\n\n- a\n- b")
    assert doc["content"][0]["type"] == "paragraph"
    assert doc["content"][1]["type"] == "bulletList"


def test_render_to_text_basic():
    doc = text_to_adf("Hi\n\n- one\n- two")
    rendered = render_to_text(doc)
    assert "Hi" in rendered
    assert "- one" in rendered
    assert "- two" in rendered


def test_render_table():
    doc = {
        "type": "doc",
        "version": 1,
        "content": [
            {
                "type": "table",
                "content": [
                    {
                        "type": "tableRow",
                        "content": [
                            {"type": "tableHeader", "content": [
                                {"type": "paragraph", "content": [{"type": "text", "text": "Tool"}]}
                            ]},
                            {"type": "tableHeader", "content": [
                                {"type": "paragraph", "content": [{"type": "text", "text": "Action"}]}
                            ]},
                        ],
                    },
                    {
                        "type": "tableRow",
                        "content": [
                            {"type": "tableCell", "content": [
                                {"type": "paragraph", "content": [{"type": "text", "text": "managePatientAllergies"}]}
                            ]},
                            {"type": "tableCell", "content": [
                                {"type": "paragraph", "content": [{"type": "text", "text": "listAllergies"}]}
                            ]},
                        ],
                    },
                ],
            }
        ],
    }
    rendered = render_to_text(doc)
    assert "| Tool | Action |" in rendered
    assert "|---|---|" in rendered
    assert "| managePatientAllergies | listAllergies |" in rendered


def test_render_to_text_marks():
    doc = {
        "type": "doc",
        "version": 1,
        "content": [
            {
                "type": "paragraph",
                "content": [
                    {"type": "text", "text": "bold", "marks": [{"type": "strong"}]},
                    {"type": "text", "text": " "},
                    {"type": "text", "text": "code", "marks": [{"type": "code"}]},
                ],
            }
        ],
    }
    rendered = render_to_text(doc)
    assert "**bold**" in rendered
    assert "`code`" in rendered


def test_sections_to_adf():
    doc = sections_to_adf([
        ("Section 1", ["bullet a", "bullet b"]),
        ("Section 2", []),
    ])
    assert doc["type"] == "doc"
    # 2 sections × (heading paragraph + bullet list) but section 2 has empty bullets so just heading.
    assert len(doc["content"]) == 3
    assert doc["content"][0]["type"] == "paragraph"
    assert doc["content"][0]["content"][0]["marks"] == [{"type": "strong"}]
    assert doc["content"][1]["type"] == "bulletList"
    assert doc["content"][2]["type"] == "paragraph"


def test_render_empty():
    assert render_to_text(None) == ""
    assert render_to_text({}) == ""
