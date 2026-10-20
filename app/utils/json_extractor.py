import json
import re
from typing import Any, Optional


def _try_parse(s: str) -> Optional[Any]:
    if not s or not isinstance(s, str):
        return None
    try:
        return json.loads(s)
    except Exception:
        try:
            # Remove trailing commas before } or ]
            cleaned = re.sub(r",\s*([}\]])", r"\1", s)
            return json.loads(cleaned)
        except Exception:
            return None


def _scan_unified(s: str) -> Optional[Any]:
    """Scans string for balanced JSON object {...} or array [...] respecting strings/escapes."""
    for i, ch in enumerate(s):
        if ch not in ("{", "["):
            continue

        stack = ["}" if ch == "{" else "]"]
        in_string = False
        escaped = False

        for j in range(i + 1, len(s)):
            c = s[j]
            if in_string:
                if escaped:
                    escaped = False
                elif c == "\\":
                    escaped = True
                elif c == '"':
                    in_string = False
                continue

            if c == '"':
                in_string = True
                continue

            if c == "{":
                stack.append("}")
            elif c == "[":
                stack.append("]")
            elif c in ("}", "]"):
                if stack and stack[-1] == c:
                    stack.pop()
                    if not stack:
                        candidate = s[i : j + 1]
                        parsed = _try_parse(candidate)
                        if parsed is not None and isinstance(parsed, (dict, list)):
                            return parsed
                        break
                else:
                    break
    return None


def parse_json(text: Any) -> Optional[Any]:
    """Strips markdown fences, extracts JSON substring with regex boundary and balanced bracket scanner."""
    if text is None:
        return None

    if isinstance(text, (dict, list)):
        return text

    if not isinstance(text, str):
        return None

    cleaned = text.strip()
    if not cleaned:
        return None

    # Strip thought tags
    cleaned = re.sub(r"<(?:thought|think)\b[^>]*>[\s\S]*?<\/(?:thought|think)>", "", cleaned, flags=re.IGNORECASE)
    # Strip markdown fences
    cleaned = re.sub(r"```[a-zA-Z0-9_-]*\n?", "", cleaned)
    cleaned = cleaned.replace("```", "").strip()

    if not cleaned:
        return None

    direct = _try_parse(cleaned)
    if direct is not None and isinstance(direct, (dict, list)):
        return direct

    # Try regex boundaries
    obj_match = re.search(r"\{[\s\S]*\}", cleaned)
    arr_match = re.search(r"\[[\s\S]*\]", cleaned)

    obj_parsed = _try_parse(obj_match.group(0)) if obj_match else None
    arr_parsed = _try_parse(arr_match.group(0)) if arr_match else None

    if obj_parsed is not None and arr_parsed is not None:
        if obj_match and arr_match:
            if obj_match.start() <= arr_match.start() and obj_match.end() >= arr_match.end():
                return obj_parsed
            if arr_match.start() <= obj_match.start() and arr_match.end() >= obj_match.end():
                return arr_parsed
            return arr_parsed if arr_match.start() < obj_match.start() else obj_parsed

    if obj_parsed is not None:
        return obj_parsed

    if arr_parsed is not None:
        return arr_parsed

    return _scan_unified(cleaned)
