"""Per-turn reply-language lock.

Pure function: given the user's message text, count Unicode codepoints in each
supported script and return the dominant language name (Hindi, Kannada, Tamil,
Bengali, Telugu, Malayalam, Gujarati, Punjabi, Odia, or English).

This is used to inject a small, unambiguous steering directive on each turn --
gpt-oss-120b's topical Hindi prior otherwise overrides the system prompt on
early English turns about Indian schemes. See `wrap_for_language_lock`.

No network calls. No LLM calls. Deterministic. Designed to be trivially
unit-testable.
"""
from __future__ import annotations

# Each entry: (codepoint range, human-language label used in the directive).
# Order does not matter -- every codepoint is checked against every range.
_SCRIPT_LABELS: list[tuple[tuple[int, int], str]] = [
    ((0x0900, 0x097F), "Hindi"),       # Devanagari
    ((0x0980, 0x09FF), "Bengali"),
    ((0x0A00, 0x0A7F), "Punjabi"),     # Gurmukhi
    ((0x0A80, 0x0AFF), "Gujarati"),
    ((0x0B00, 0x0B7F), "Odia"),
    ((0x0B80, 0x0BFF), "Tamil"),
    ((0x0C00, 0x0C7F), "Telugu"),
    ((0x0C80, 0x0CFF), "Kannada"),
    ((0x0D00, 0x0D7F), "Malayalam"),
]


def detect_reply_language(text: str) -> str:
    """Return the human-language label for the dominant script in `text`.

    Falls back to "English" when no letters are present (punctuation-only,
    empty string). Latin letters (a-z, A-Z) count as English; every recognised
    Indic codepoint counts as that script's language. Whichever bucket has
    the most characters wins.
    """
    counts: dict[str, int] = {}
    for ch in text:
        cp = ord(ch)
        for (lo, hi), label in _SCRIPT_LABELS:
            if lo <= cp <= hi:
                counts[label] = counts.get(label, 0) + 1
                break
    latin = sum(1 for ch in text if "a" <= ch.lower() <= "z")
    if latin:
        counts["English"] = latin
    if not counts:
        return "English"
    return max(counts.items(), key=lambda kv: kv[1])[0]


# The prefix that identifies a wrapped message. Kept as a constant so history
# can be un-wrapped by exact-match check rather than a fragile regex.
_WRAP_PREFIX = "[Reply-language lock: "


def wrap_for_language_lock(text: str) -> str:
    """Prepend a short, per-turn steering directive to the user's message.

    The directive names the language the user just wrote in and demands the
    reply be in that same language. It goes only to the model -- the frontend
    never sees it, and callers strip it back out of conversation history so
    future turns start clean (`unwrap_from_history`).
    """
    lang = detect_reply_language(text)
    return (
        f"{_WRAP_PREFIX}{lang}. The user's most recent message is written in "
        f"{lang}. Reply in {lang}, using the same script. Ignore the language "
        f"of any earlier turn.]\n\n{text}"
    )


def unwrap_from_history(agent, original_text: str) -> None:
    """Replace the wrapped user text in the agent's last user message with the
    original, so the steering directive does not persist across turns.

    Best-effort: silently does nothing if the message shape has changed or the
    text was not wrapped (the wrap prefix is exact-match checked).
    """
    messages = getattr(agent, "messages", None)
    if not messages:
        return
    for msg in reversed(messages):
        if isinstance(msg, dict) and msg.get("role") == "user":
            for block in msg.get("content") or []:
                if isinstance(block, dict) and "text" in block:
                    if block["text"].startswith(_WRAP_PREFIX):
                        block["text"] = original_text
                    return
            return
