"""Shipping reference helpers: ISO 6346 container numbers and reference-type detection."""

from __future__ import annotations

import re

from integrations.carriers.models import ReferenceKind

_CONTAINER_RE = re.compile(r"^[A-Z]{3}[UJZ][0-9]{7}$")
# ISO 6346 letter values skip multiples of 11 (11, 22, 33).
_LETTER_VALUES = dict(
    zip(
        "ABCDEFGHIJKLMNOPQRSTUVWXYZ",
        [10, *range(12, 22), *range(23, 33), *range(34, 39)],
        strict=True,
    )
)


def normalize_reference(value: str) -> str:
    return re.sub(r"[\s\-./]", "", value).upper()


def iso6346_check_digit(first_ten: str) -> int:
    """Check digit for the owner code + category + serial (10 characters)."""
    if len(first_ten) != 10:
        raise ValueError("expected 10 characters")
    total = 0
    for i, ch in enumerate(first_ten):
        v = _LETTER_VALUES[ch] if ch.isalpha() else int(ch)
        total += v * (2**i)
    return total % 11 % 10


def is_valid_container_number(value: str) -> bool:
    v = normalize_reference(value)
    return bool(_CONTAINER_RE.match(v)) and iso6346_check_digit(v[:10]) == int(v[10])


def looks_like_container(value: str) -> bool:
    return bool(_CONTAINER_RE.match(normalize_reference(value)))


def detect_reference_type(value: str) -> ReferenceKind:
    """Best-effort guess; callers should prefer an explicit type when they know it."""
    return "container" if looks_like_container(value) else "booking"
