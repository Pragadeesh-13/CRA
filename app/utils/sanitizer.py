import re

NON_SPEAKER_HEADERS = re.compile(
    r"^\s*(?:Note|Warning|Caution|Important|Notice|However|Therefore|Summary|Verdict|Clause|Section)\s*:",
    re.IGNORECASE,
)

SPEAKER_PREFIX_REGEX = re.compile(
    r"^\s*(?:(?:\*{1,2}|\[)?([A-Za-z\s]+(?:Council|Representative|Agent|Speaker)|[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\s*(?:\([^)]*\))?\s*:\s*(?:\*{1,2}|\])?\s*|(?:\*{1,2}|\[)([A-Za-z\s]+(?:Council|Representative|Agent|Speaker)|[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\s*(?:\([^)]*\))?(?:\*{1,2}|\])\s*:\s*)",
    re.UNICODE,
)

INLINE_PREAMBLE_REGEX = re.compile(
    r"^\s*(?:okay|sure|certainly|understood)[,\s]+(?:(?:here is|here's)\s+(?:our|the|my)\s+stance\s*:\s*)?",
    re.IGNORECASE,
)

STANDALONE_STANCE_INTRO = re.compile(
    r"^\s*(?:here is|here's)\s+(?:our|the|my)\s+(?:stance|analysis|response|verdict)\s*:\s*",
    re.IGNORECASE,
)

PREAMBLE_REGEX = re.compile(
    r"^\s*(?:okay|sure|certainly|understood|first(?:ly|,|\s+of\s+all|\s+let\s+me|\s+i\b)|let me|i need to|here is|here's|i should|i will|to answer|analysis|i'm going to|as requested)\b",
    re.IGNORECASE,
)

REASONING_PATTERN = re.compile(
    r"\b(i need to|let me|i should|i will|to answer|analysis|parse through|first,?\s*i|first,?\s*we)\b",
    re.IGNORECASE,
)


def sanitize_model_output(text: str) -> str:
    """Strips <thought>/<think> tags, markdown fences, preambles, and speaker prefixes."""
    if not text or not isinstance(text, str):
        return ""

    clean = text.strip()
    if not clean:
        return ""

    # Strip closed thought blocks
    clean = re.sub(r"<(?:thought|think)\b[^>]*>[\s\S]*?<\/(?:thought|think)>", "", clean, flags=re.IGNORECASE)

    # Strip unclosed thought blocks at end
    clean = re.sub(r"<(?:thought|think)\b[^>]*>[\s\S]*$", "", clean, flags=re.IGNORECASE)

    # Strip stray thought tags
    clean = re.sub(r"<\/?(?:think|thought)\b[^>]*>", "", clean, flags=re.IGNORECASE)

    # Strip markdown code fences
    clean = re.sub(r"```[a-zA-Z0-9_-]*\n?", "", clean)
    clean = clean.replace("```", "")

    clean = clean.strip()
    if not clean:
        return ""

    # Strip transition dividers like ****, ---, ___
    clean = re.sub(r"^\s*(?:\*{3,}|-{3,}|_{3,})\s*", "", clean)

    # Strip speech section headers like (Public Speech), **Public Speech:**, etc.
    clean = re.sub(
        r"^\s*(?:(?:\*{1,2}|\[|\()?\s*(?:Public\s+)?(?:Speech|Statement|Verdict|Decision)\s*(?:\([^)]*\))?\s*(?:\*{1,2}|\]|\))?\s*:?\s*)",
        "",
        clean,
        flags=re.IGNORECASE,
    )
    clean = re.sub(r"^\s*(?:\*{3,}|-{3,}|_{3,})\s*", "", clean)

    clean = clean.strip()
    if not clean:
        return ""

    clean = INLINE_PREAMBLE_REGEX.sub("", clean)
    clean = STANDALONE_STANCE_INTRO.sub("", clean)
    clean = clean.strip()
    if not clean:
        return ""

    lines = clean.split("\n")
    while len(lines) > 1 and PREAMBLE_REGEX.search(lines[0]) and ":" not in lines[0]:
        lines.pop(0)

    clean = "\n".join(lines).strip()
    if not clean:
        return ""

    if SPEAKER_PREFIX_REGEX.search(clean) and not NON_SPEAKER_HEADERS.search(clean):
        clean = SPEAKER_PREFIX_REGEX.sub("", clean).strip()

    return clean


def format_debate_summary(text: str) -> str:
    """Formats multi-turn debate transcripts into a concise 1-2 sentence consensus summary."""
    cleaned = sanitize_model_output(text)
    if not cleaned:
        return ""

    filtered_lines = []
    for line in cleaned.split("\n"):
        line_str = line.strip()
        if not line_str:
            continue
        if re.search(r"^[-•]\s*.+Council\s*\([^)]*\):", line_str, re.IGNORECASE):
            continue
        if re.search(r"^[-•]\s*.+:\s*", line_str):
            continue
        if re.search(r"<\/?(?:think|thought)\b[^>]*>", line_str, re.IGNORECASE):
            continue
        filtered_lines.append(line_str)

    merged = " ".join(filtered_lines)
    merged = re.sub(r"\s+", " ", merged).strip()
    if not merged:
        return "Council reached no clear consensus."

    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", merged) if s.strip()]
    content_sentences = [s for s in sentences if not REASONING_PATTERN.search(s)]

    if len(content_sentences) >= 2:
        return f"{content_sentences[0]} {content_sentences[1]}".strip()
    if len(content_sentences) == 1:
        return content_sentences[0].strip()
    if len(sentences) >= 2:
        return f"{sentences[0]} {sentences[1]}".strip()
    return merged
