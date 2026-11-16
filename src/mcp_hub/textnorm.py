"""Text folding for matching: lower case and no diacritics. Leaf module: no mcp_hub imports."""
from __future__ import annotations

import unicodedata


def fold(text: str) -> str:
    """Casefold and strip diacritics (for example 'H\u00f4m nay' -> 'hom nay').

    The two legacy copies (bitnet/rules.py and plan_merge.py) mapped U+0110 to "d" and "D" respectively; casefold makes
    both the same, so one definition serves both.
    """
    text = text.replace("\u0111", "d").replace("\u0110", "D")
    return "".join(c for c in unicodedata.normalize("NFD", text) if not unicodedata.combining(c)).casefold()
