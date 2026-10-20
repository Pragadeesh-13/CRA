from app.utils.thought_buffer import ThoughtBuffer


def test_thought_buffer_suppresses_inside_tags():
    deltas = []
    tb = ThoughtBuffer(on_delta=lambda full, chunk: deltas.append(chunk))

    tb.feed("<thought>This is an internal reasoning process.")
    assert len(deltas) == 0

    tb.feed(" We should oppose the policy.</thought>")
    tb.feed(" Fellow council members, Tamil Nadu opposes this.")

    assert len(deltas) > 0
    full_speech = "".join(deltas)
    assert "<thought>" not in full_speech
    assert "Fellow council members, Tamil Nadu opposes this." in full_speech


def test_thought_buffer_emits_directly_when_no_tags():
    deltas = []
    tb = ThoughtBuffer(threshold=20, on_delta=lambda full, chunk: deltas.append(chunk))

    # Feeds text longer than threshold without thought tags
    tb.feed("We strongly support this federal renewable initiative because it benefits our industry.")
    assert len(deltas) > 0
    assert "We strongly support" in "".join(deltas)


def test_thought_buffer_flush_unclosed_thought():
    tb = ThoughtBuffer()
    tb.feed("<thought>Unclosed thinking...")
    flushed = tb.flush()
    # When thought tag was opened but never closed, flush drops internal thought
    assert flushed == ""


def test_thought_buffer_chunk_split_tags_and_transition_junk():
    public_deltas = []
    reasoning_deltas = []
    tb = ThoughtBuffer(
        on_delta=lambda chunk: public_deltas.append(chunk),
        on_reasoning_delta=lambda chunk: reasoning_deltas.append(chunk),
    )

    # Chunk-split opening tag
    tb.feed("<")
    tb.feed("th")
    tb.feed("ought>\n")
    assert len(public_deltas) == 0

    # Reasoning content
    tb.feed("Analyze: threats and allies.\n")
    tb.feed("Angle of attack: focus on farmers.\n")
    assert len(public_deltas) == 0
    assert len(reasoning_deltas) > 0

    # Chunk-split closing tag
    tb.feed("</th")
    tb.feed("ought>\n\n")

    # Transition junk: asterisks divider and (Public Speech) header
    tb.feed("****\n\n")
    tb.feed("(Public Speech)\n\n")
    assert len(public_deltas) == 0

    # Actual public speech
    tb.feed("The positive declarations regarding IT hubs ")
    tb.feed("are impressive metrics.")
    tb.flush()

    full_public = "".join(public_deltas)
    full_reasoning = "".join(reasoning_deltas)

    # Verify no thought tags or transition junk in public speech
    assert "<thought>" not in full_public
    assert "</thought>" not in full_public
    assert "****" not in full_public
    assert "Public Speech" not in full_public
    assert "The positive declarations regarding IT hubs are impressive metrics." in full_public

    # Verify reasoning was captured
    assert "Analyze: threats and allies." in full_reasoning
    assert "</thought>" not in full_reasoning
