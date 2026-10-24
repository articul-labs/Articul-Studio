"""Vietnamese text normalization: turn written forms into speakable words.

A TTS model must not see "2025", "50%", "7:30" or "120.000đ" as raw tokens —
grapheme-to-sound for digits is unreliable. This module rewrites numbers,
currency, percentages and clock times into Vietnamese words before synthesis.
Diacritics are preserved; nothing else in the text is altered.
"""

from __future__ import annotations

import re

_DIGITS = ["không", "một", "hai", "ba", "bốn", "năm", "sáu", "bảy", "tám", "chín"]

_CURRENCY = {
    "đ": "đồng",
    "vnd": "đồng",
    "vnđ": "đồng",
    "k": "nghìn đồng",
    "usd": "đô la",
    "$": "đô la",
}

# Long distance/area units read out with their Vietnamese names.
_UNITS = {
    "km": "ki lô mét",
    "kg": "ki lô gam",
    "cm": "xăng ti mét",
    "mm": "mi li mét",
}

_NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)*")
_TIME_RE = re.compile(r"\b(\d{1,2})[:h](\d{2})\b")
# Trailing lookahead (not \b): '%' and 'đ' are non-word chars, so \b never matches after them.
_TOKEN_RE = re.compile(r"(\d[\d.,]*)\s*(%|đ|vnđ|vnd|usd|\$|km|kg|cm|mm|k)(?![a-zA-Z0-9])", re.IGNORECASE)


def _read_below_hundred(n: int, preceded: bool) -> str:
    """Read 0..99. `preceded` means a hundreds word comes before the tens."""
    if n == 0:
        return ""
    tens, unit = divmod(n, 10)
    if tens == 0:
        return _DIGITS[unit]
    tens_word = "mười" if tens == 1 else f"{_DIGITS[tens]} mươi"
    if unit == 0:
        return tens_word
    if unit == 1:
        # 21 -> "hai mươi mốt", but 101 -> "một trăm lẻ một" keeps "một".
        unit_word = "mốt" if tens >= 2 else "một"
    elif unit == 5:
        # 15 -> "mười lăm", 25 -> "hai mươi lăm".
        unit_word = "lăm"
    elif unit == 4 and tens >= 2:
        unit_word = "tư"
    else:
        unit_word = _DIGITS[unit]
    return f"{tens_word} {unit_word}"


def _read_group(n: int, preceded: bool) -> str:
    """Read 0..999 as one three-digit group. `preceded` adds the 'lẻ'/'không trăm' rules."""
    hundreds, rest = divmod(n, 100)
    parts: list[str] = []
    if hundreds > 0:
        parts.append(f"{_DIGITS[hundreds]} trăm")
    elif preceded:
        # 2,001 -> "hai nghìn không trăm lẻ một".
        parts.append("không trăm")
    below = _read_below_hundred(rest, preceded=hundreds > 0 or preceded)
    if below:
        if rest < 10 and (hundreds > 0 or preceded):
            parts.append(f"lẻ {below}")
        else:
            parts.append(below)
    return " ".join(p for p in parts if p)


def _group_word(index: int, total_groups: int) -> str:
    """Scale word for a 3-digit group: ... tỷ | triệu | nghìn | units."""
    from_units = total_groups - 1 - index
    return ["", " nghìn", " triệu", " tỷ"][from_units] if from_units <= 3 else ""


def number_to_words(number_str: str) -> str:
    """Convert digits like '2025', '1,050,000' or '3.5' to Vietnamese words."""
    if "." in number_str and len(number_str.split(".", 1)[1]) != 3:
        return _read_decimal(number_str)
    if "," in number_str and len(number_str.split(",", 1)[1]) != 3:
        return _read_decimal(number_str.replace(",", "."))
    digits = number_str.replace(",", "").replace(".", "")
    if not digits.isdigit():
        return number_str
    n = int(digits)
    if n == 0:
        return "không"
    groups: list[int] = []
    while n:
        groups.append(n % 1000)
        n //= 1000
    groups.reverse()
    words: list[str] = []
    for i, g in enumerate(groups):
        if g == 0:
            continue
        words.append(_read_group(g, preceded=bool(words)))
        scale = _group_word(i, len(groups))
        if scale:
            words.append(scale.strip())
    return " ".join(words)


def _read_decimal(number_str: str) -> str:
    """'3.5' -> 'ba phẩy năm' (Vietnamese comma/point as decimal separator)."""
    normalized = number_str.replace(",", ".")
    whole, _, frac = normalized.partition(".")
    frac_words = " ".join(_DIGITS[int(d)] for d in frac if d.isdigit())
    return f"{number_to_words(whole)} phẩy {frac_words}"


def _convert_time(m: re.Match) -> str:
    hour, minute = int(m.group(1)), int(m.group(2))
    hour = hour % 24 if hour > 12 else hour
    return f"{_DIGITS[hour]} giờ {_read_below_hundred(minute, preceded=False)}".strip()


def _convert_token(m: re.Match) -> str:
    amount, unit = m.group(1), m.group(2)
    amount_words = number_to_words(amount)
    unit_lower = unit.lower()
    if unit == "%":
        return f"{amount_words} phần trăm"
    if unit_lower in _CURRENCY:
        return f"{amount_words} {_CURRENCY[unit_lower]}"
    return f"{amount_words} {_UNITS[unit_lower]}"


def normalize(text: str) -> str:
    """Rewrite times, unit/currency/percent tokens, then bare numbers."""
    text = _TIME_RE.sub(_convert_time, text)
    text = _TOKEN_RE.sub(_convert_token, text)
    return _NUMBER_RE.sub(lambda m: number_to_words(m.group(0)), text)


def split_sentences(text: str) -> list[str]:
    """Split into speakable chunks, keeping the delimiter on each sentence."""
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    return [p for p in parts if p]
