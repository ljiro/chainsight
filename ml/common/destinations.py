"""Resolve free-text AIS destinations to UN/LOCODE ports.

AIS destinations are typed by crews ("NL RTM", "SGSIN", "ROTTERDAM", "FOR ORDERS"). Only
unambiguous forms are resolved: UN/LOCODE-shaped text (with or without a separator) or an
exact, unique port name. Everything else stays unresolved; no fuzzy guessing.
"""

from __future__ import annotations

import re
from collections.abc import Mapping

_CODE = re.compile(r"^([A-Z]{2})[\s\-_./]?([A-Z2-9]{3})$")


def resolve_destination(
    text: str | None,
    known_codes: set[str] | frozenset[str],
    unique_names: Mapping[str, str],
) -> tuple[str | None, str | None]:
    """Return (unlocode, method) where method is 'unlocode' or 'name', else (None, None)."""
    if not text:
        return None, None
    t = re.sub(r"\s+", " ", text.upper().strip())
    # Crews often write "XX YYY>ZZ AAA" or "XXYYY VIA ..."; take the final leg / first token.
    t = t.split(">")[-1].strip()
    m = _CODE.match(t) or _CODE.match(t.split(" VIA ")[0].strip())
    if m:
        code = m.group(1) + m.group(2)
        if code in known_codes:
            return code, "unlocode"
    code = unique_names.get(t)
    return (code, "name") if code else (None, None)
