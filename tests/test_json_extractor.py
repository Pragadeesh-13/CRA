from app.utils.json_extractor import parse_json


def test_parse_json_direct():
    raw = '{"selected": ["Tamil Nadu", "Gujarat"], "benched": ["Delhi"]}'
    res = parse_json(raw)
    assert res == {"selected": ["Tamil Nadu", "Gujarat"], "benched": ["Delhi"]}


def test_parse_json_with_markdown_fences():
    raw = "```json\n{\"reply_state\": \"Karnataka\"}\n```"
    res = parse_json(raw)
    assert res == {"reply_state": "Karnataka"}


def test_parse_json_embedded_in_text():
    raw = "Here is the result of moderator analysis:\n{\"winner\": \"Maharashtra\", \"loser\": \"Punjab\"}\nThank you."
    res = parse_json(raw)
    assert res == {"winner": "Maharashtra", "loser": "Punjab"}


def test_parse_json_with_trailing_commas():
    raw = '{"positive": ["Gujarat", "Maharashtra", ], "negative": ["Kerala", ], }'
    res = parse_json(raw)
    assert res == {"positive": ["Gujarat", "Maharashtra"], "negative": ["Kerala"]}


def test_parse_json_with_thought_tags():
    raw = "<thought>Thinking about states</thought>{\"rebuttal_panel\": [\"Delhi\", \"Punjab\", \"Assam\"]}"
    res = parse_json(raw)
    assert res == {"rebuttal_panel": ["Delhi", "Punjab", "Assam"]}
