"""Bounded chat-expression recognition and Unicode grapheme protection.

These helpers do not interpret arbitrary parenthesized text as an emoticon.
Unicode graphemes use regex's grapheme matcher, not hand-built emoji ranges.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterator

import regex

# Punctuation with common conversational or quantitative meaning. All Unicode
# symbol categories (S*) are kept separately in natural mode.
NATURAL_KEEP_SYMBOLS = frozenset("~～…%％‰‱@#&/\\_*-")
_GRAPHEME = regex.compile(r"\X")
_EMOJI = regex.compile(r"\p{Extended_Pictographic}|\p{Regional_Indicator}|\u20e3")

# Candidates are bounded, nonrecursive and cannot cross lines. A separate
# validator rejects ordinary bracketed words, notes and arithmetic expressions.
_LITERAL_FACES = ("¯\\_(ツ)_/¯", "┐(´д`)┌", "(╯°□°）╯︵ ┻━┻")
_FACE_PATTERN = re.compile(
    "|".join(re.escape(face) for face in _LITERAL_FACES)
    + r"|[ヽヾ╰╯]?\([^()\r\n]{1,40}\)[ノﾉヽヾづっつوง╯╰ゞ♡❤✧]{0,4}"
    + r"|[ヽヾ╰╯]?（[^（）\r\n]{1,40}）[ノﾉヽヾづっつوง╯╰ゞ♡❤✧]{0,4}"
    + r"|ʕ[^ʕʔ\r\n]{1,32}ʔ"
    + r"|(?<![A-Za-z0-9])(?:[:;=8](?:[-^]|['’]-?)?[)(DPpOo/\\|]"
    + r"|[xX][dD]|[TtQqOo0xX^>][_﹏.ωwWvV-][TtQqOo0xX^<]"
    + r"|[Qq][AaWw][Qq]|</?3)(?![A-Za-z0-9])"
)
_FACE_LETTERS = frozenset(
    "TtOoQqXxUuWwVvωΩДд∀∇▽益皿艸口ツシノᗜಠಥರʕʔᴥつづㅂ๑งوヽヾﾉっゞ"
)
_FACE_FEATURES = _FACE_LETTERS | frozenset("^><_﹏•●○◕◔◉°ﾟ｡;；╥〒ಥಥ0")


def _is_face(value: str) -> bool:
    if value in _LITERAL_FACES:
        return True
    if not any(left in value and right in value for left, right in (("(", ")"), ("（", "）"), ("ʕ", "ʔ"))):
        # Non-bracketed alternatives were matched against explicit patterns.
        return True
    if re.fullmatch(r"[(（]\s*[+-]?\d[\d\s.+*/%=<>!^|&×÷＋−~:-]*[)）]", value):
        return False
    body = value
    for char in body:
        if char.isalnum() and char not in _FACE_LETTERS and char != "0":
            return False
    return sum(char in _FACE_FEATURES for char in body) >= 2


def _faces(text: str):
    for match in _FACE_PATTERN.finditer(text):
        if _is_face(match.group()):
            yield match


def _graphemes(text: str) -> Iterator[tuple[str, bool]]:
    for match in _GRAPHEME.finditer(text):
        value = match.group()
        yield value, bool(_EMOJI.search(value))


def chat_units(text: str) -> Iterator[tuple[str, bool]]:
    """Yield whole graphemes; recognized emoji/emoticons are protected atoms."""
    cursor = 0
    for match in _faces(text):
        yield from _graphemes(text[cursor:match.start()])
        yield match.group(), True
        cursor = match.end()
    yield from _graphemes(text[cursor:])


def mask_emoticons(text: str) -> str:
    """Mask recognized faces for Markdown detection only, never for delivery."""
    parts: list[str] = []
    cursor = 0
    for match in _faces(text):
        # In [label](T_T), the parentheses are link syntax, not a chat face.
        # Keep that syntax intact so a real link/image still triggers rendering.
        if match.start() and text[match.start() - 1] == "]":
            continue
        parts.extend((text[cursor:match.start()], "表情"))
        cursor = match.end()
    parts.append(text[cursor:])
    return "".join(parts)


def keep_naturally(unit: str) -> bool:
    return unit[0] in NATURAL_KEEP_SYMBOLS or unicodedata.category(unit[0]).startswith("S")
