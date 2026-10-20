from app.utils.sanitizer import sanitize_model_output, format_debate_summary


def test_sanitize_thought_tags():
    raw = "<thought>Internal agent reasoning about alliances.</thought>Tamil Nadu strongly supports industrial growth."
    assert sanitize_model_output(raw) == "Tamil Nadu strongly supports industrial growth."


def test_sanitize_unclosed_thought():
    raw = "Public opening statement. <thought>Unfinished thought"
    assert sanitize_model_output(raw) == "Public opening statement."


def test_sanitize_code_fences():
    raw = "```json\n{\"stance\": \"support\"}\n```"
    assert sanitize_model_output(raw) == '{"stance": "support"}'


def test_sanitize_preambles():
    raw = "Sure, here is our stance:\nWe demand proportional central allocation."
    assert sanitize_model_output(raw) == "We demand proportional central allocation."


def test_sanitize_speaker_prefix():
    raw = "Maharashtra Council: We contribute 15% of national GDP."
    assert sanitize_model_output(raw) == "We contribute 15% of national GDP."


def test_format_debate_summary():
    raw = (
        "<thought>Synthesizing rounds</thought>\n"
        "The council examined regional tax devolution. "
        "Industrial states demanded retention of local revenue while consuming states favored centralized distribution."
    )
    summary = format_debate_summary(raw)
    assert "tax devolution" in summary
    assert "<thought>" not in summary


def test_sanitize_dividers_and_public_speech_header():
    raw = """<thought>
Analyze: Who is your biggest threat?
</thought>
****

(Public Speech)

The positive declarations regarding IT hubs and petrochemical growth are impressive metrics for capital investors."""
    cleaned = sanitize_model_output(raw)
    assert "<thought>" not in cleaned
    assert "</thought>" not in cleaned
    assert "****" not in cleaned
    assert "Public Speech" not in cleaned
    assert cleaned.startswith("The positive declarations regarding IT hubs")
