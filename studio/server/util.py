"""Small helpers shared across server modules."""

from __future__ import annotations


def ascii_filename(name: str, suffix: str, fallback: str) -> str:
    """HTTP header values must be ASCII; non-ASCII names get an ASCII fallback."""
    ascii_name = "".join(c for c in name if c.isascii() and (c.isalnum() or c in " -_")).strip()
    return (ascii_name or fallback) + suffix
