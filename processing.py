"""Pure reply policy. No AstrBot or browser process is needed here."""

from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass, fields
from typing import Any, Mapping

from .symbols import chat_units, keep_naturally, mask_emoticons

DEFAULT_SPLIT_SYMBOLS = "\n。.！？!?；;，,、：:…~～|｜"
_THINK_TAG = re.compile(r"<\s*(/?)\s*(think|thinking|analysis)\s*>", re.I)
_PROTECTED = re.compile(
    r"https?://[^\s<>\"'，。！？；、]+"
    r"|[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}"
    r"|[+-]?\d+(?:[.:/\-]\d+)+(?:[%％])?"
    r"|[+-]?\d+[%％]"
    r"|[+-]\d+",
    re.I,
)
_URL_TRAILING = ".,!?;:，。！？；："
_URL_PAIRS = {")": "(", "]": "[", "}": "{", "）": "（", "】": "【", "》": "《"}


def _trim_url(value: str) -> str:
    while value:
        final = value[-1]
        if final in _URL_TRAILING:
            value = value[:-1]
        elif final in _URL_PAIRS and value.count(final) > value.count(_URL_PAIRS[final]):
            value = value[:-1]
        else:
            break
    return value


@dataclass(frozen=True)
class Settings:
    enabled: bool = True
    force_non_streaming: bool = True
    long_text_threshold: int = 150
    render_mode: str = "image"
    auto_pdf_threshold: int = 3000
    pdf_on_image_error: bool = True
    split_symbols: str = DEFAULT_SPLIT_SYMBOLS
    strip_symbols: bool = True
    symbol_filter_mode: str = "strict"
    custom_keep_symbols: str = ""
    custom_remove_symbols: str = ""
    preserve_urls_numbers: bool = True
    remove_think: bool = True
    blocked_words: tuple[str, ...] = ()
    max_segment_length: int = 150
    max_messages: int = 10  # Soft Plain-segment cap; never discard attachments.
    send_mode: str = "segments"
    send_interval: float = 0.6
    render_timeout: float = 30.0
    image_width: int = 960
    font_size: int = 22
    max_render_chars: int = 100000
    cache_retention_hours: int = 24

    @classmethod
    def from_mapping(cls, config: Mapping[str, Any]) -> Settings:
        values = {f.name: config[f.name] for f in fields(cls) if f.name in config}
        for name in (
            "enabled", "force_non_streaming", "pdf_on_image_error", "strip_symbols",
            "preserve_urls_numbers", "remove_think",
        ):
            if name in values and type(values[name]) is not bool:
                raise ValueError(f"{name} 必须是布尔值")
        bounds = {
            "long_text_threshold": (1, 100000),
            "auto_pdf_threshold": (1, 100000),
            "max_segment_length": (1, 10000),
            "max_messages": (1, 50),
            "image_width": (320, 1920),
            "font_size": (12, 48),
            "max_render_chars": (1000, 500000),
            "cache_retention_hours": (1, 168),
        }
        for name, (low, high) in bounds.items():
            if name in values:
                value = values[name]
                if type(value) is not int or not low <= value <= high:
                    raise ValueError(f"{name} 必须是 {low}～{high} 的整数")
        for name, low, high in (("send_interval", 0, 10), ("render_timeout", 0.1, 300)):
            if name in values:
                value = values[name]
                if (
                    isinstance(value, bool)
                    or not isinstance(value, (int, float))
                    or not low <= value <= high
                    or not math.isfinite(value)
                ):
                    raise ValueError(f"{name} 必须是 {low}～{high} 的有限数值")
        if values.get("render_mode", "image") not in ("image", "pdf", "auto"):
            raise ValueError("render_mode 必须为 image、pdf 或 auto")
        if values.get("send_mode", "segments") not in ("segments", "core"):
            raise ValueError("send_mode 必须为 segments 或 core")
        if "split_symbols" in values and not isinstance(values["split_symbols"], str):
            raise ValueError("split_symbols 必须是字符串，不是正则表达式")
        if values.get("symbol_filter_mode", "strict") not in ("strict", "natural", "custom"):
            raise ValueError("symbol_filter_mode 必须为 strict、natural 或 custom")
        for name in ("custom_keep_symbols", "custom_remove_symbols"):
            if name in values:
                value = values[name]
                if (
                    not isinstance(value, str) or len(value) > 256
                    or any(unicodedata.category(char) == "Cc" for char in value)
                ):
                    raise ValueError(f"{name} 必须是不含控制字符、至多256字符的字符串")
        words = values.get("blocked_words", ())
        if not isinstance(words, (list, tuple)) or any(
            not isinstance(word, str) or not word.strip() for word in words
        ):
            raise ValueError("blocked_words 必须是非空字符串列表")
        values["blocked_words"] = tuple(words)
        return cls(**values)


def remove_thinking(text: str, stack: list[str] | None = None) -> str:
    """Drop think blocks; an optional stack continues across non-text components."""
    parts: list[str] = []
    if stack is None:
        stack = []
    cursor = 0
    for match in _THINK_TAG.finditer(text):
        if not stack:
            parts.append(text[cursor:match.start()])
        closing, name = match.group(1), match.group(2).lower()
        if closing:
            if stack and stack[-1] == name:
                stack.pop()
        else:
            stack.append(name)
        cursor = match.end()
    if not stack:
        parts.append(text[cursor:])
    return "".join(parts)


def clean_text(
    text: str, settings: Settings, thinking_stack: list[str] | None = None,
) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if settings.remove_think:
        text = remove_thinking(text, thinking_stack)
    text = "".join(
        char for char in text
        if (unicodedata.category(char) != "Cc" or char in "\n\t")
        and char not in "\ufeff\u200b"
    )
    for word in settings.blocked_words:
        text = text.replace(word, "")
    return text.strip()


def visible_length(text: str) -> int:
    """Count non-whitespace Unicode code points, including punctuation."""
    return sum(not char.isspace() for char in text)


def render_kind(text: str, has_markdown: bool, settings: Settings) -> str | None:
    length = visible_length(text)
    if length <= settings.long_text_threshold and not has_markdown:
        return None
    if settings.render_mode == "pdf":
        return "pdf"
    if settings.render_mode == "auto" and length > settings.auto_pdf_threshold:
        return "pdf"
    return "image"


def markdown_probe(text: str, settings: Settings) -> str:
    """Prevent chat faces such as >_< from looking like Markdown blockquotes."""
    if settings.symbol_filter_mode == "strict":
        return text
    return mask_emoticons(text)


def _units(text: str, protect: bool, chat: bool = False):
    """Yield (text, origin) units: plain, chat atom, or protected URL/number."""
    def plain(value: str):
        if chat:
            yield from ((unit, "chat" if atom else "plain") for unit, atom in chat_units(value))
        else:
            yield from ((char, "plain") for char in value)

    if not protect:
        yield from plain(text)
        return
    cursor = 0
    for match in _PROTECTED.finditer(text):
        # A numeric prefix such as +1 in +1️⃣ must not detach a following
        # variation selector/keycap/combining mark from its grapheme.
        if chat and match.end() < len(text) and (
            unicodedata.category(text[match.end()]).startswith("M")
            or text[match.end()] == "\u200d"
        ):
            continue
        yield from plain(text[cursor:match.start()])
        value = match.group()
        protected = _trim_url(value) if value.lower().startswith("http") else value
        if protected:
            yield protected, "url_number"
        yield from plain(value[len(protected):])
        cursor = match.end()
    yield from plain(text[cursor:])


def split_short_text(text: str, settings: Settings) -> list[str]:
    """Split short replies using a backwards-compatible or chat-safe policy.

    URLs/numbers have highest priority. Chat mode also protects graphemes and
    known faces; retained symbols override delimiters. Custom removal overrides
    retention, but a removed ordinary delimiter still creates a boundary.
    """
    delimiters = set(settings.split_symbols)
    chat = settings.symbol_filter_mode != "strict"
    custom = settings.symbol_filter_mode == "custom"
    keep = set(settings.custom_keep_symbols) if custom else set()
    remove = set(settings.custom_remove_symbols) if custom and settings.strip_symbols else set()
    segments: list[str] = []
    current: list[str] = []
    length = 0

    def flush() -> None:
        nonlocal length
        value = "".join(current).strip()
        if value:
            segments.append(value)
        current.clear()
        length = 0

    for unit, origin in _units(text, settings.preserve_urls_numbers, chat):
        if origin != "url_number":
            removed = bool(remove.intersection(unit))
            retained = chat and (
                origin == "chat" or keep_naturally(unit) or bool(keep.intersection(unit))
            ) and not removed
            if not retained and origin != "chat" and unit[0] in delimiters:
                flush()
                continue
            if removed or (
                not retained and settings.strip_symbols
                and unicodedata.category(unit[0])[0] in "PS"
            ):
                continue
        if length and length + len(unit) > settings.max_segment_length:
            flush()
        current.append(unit)
        length += len(unit)
    flush()
    return segments


def cap_messages(items: list[Any], limit: int) -> list[list[Any]]:
    """Keep order and all content; group the excess into the final message."""
    if len(items) <= limit:
        return [[item] for item in items]
    return [[item] for item in items[:limit - 1]] + [items[limit - 1:]]


def fallback_chunks(text: str, size: int = 1000) -> list[str]:
    """Lossless fallback: don't destroy Markdown or code when rendering fails."""
    return [text[index:index + size] for index in range(0, len(text), size)]
