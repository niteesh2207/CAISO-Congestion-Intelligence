from __future__ import annotations

from dataclasses import dataclass
import re

from .object_resolver import BranchIdentity


@dataclass(frozen=True)
class LMPSpreadRequest:
    source_bus: int
    sink_bus: int


def parse_lmp_spread(text: str) -> LMPSpreadRequest | None:
    patterns = [
        r"(?:lmp|price)\s+spread\s+(?:from\s+)?(?:bus\s*)?(\d+)\s+(?:to|->)\s+(?:bus\s*)?(\d+)",
        r"(?:between)\s+(?:bus\s*)?(\d+)\s+(?:and)\s+(?:bus\s*)?(\d+)",
        r"(?:from)\s+(?:bus\s*)?(\d+)\s+(?:to)\s+(?:bus\s*)?(\d+)",
    ]
    for pattern in patterns:
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            return LMPSpreadRequest(int(m.group(1)), int(m.group(2)))

    # "Why is bus 401 more expensive than bus 101?"
    m = re.search(
        r"(?:why\s+(?:is|does)\s+)?bus\s*(\d+).*?(?:more expensive|higher|premium).*?bus\s*(\d+)",
        text,
        re.IGNORECASE,
    )
    if m:
        return LMPSpreadRequest(source_bus=int(m.group(2)), sink_bus=int(m.group(1)))
    return None


def parse_constraint_branch(text: str) -> BranchIdentity | None:
    m = re.search(
        r"(?:branch|line|constraint)\s*(\d+)\s*[-–—]\s*(\d+)"
        r"(?:\s+(?:circuit|ckt)\s*([A-Za-z0-9&]+))?",
        text,
        re.IGNORECASE,
    )
    if not m:
        return None
    return BranchIdentity(int(m.group(1)), int(m.group(2)), m.group(3) or "1")
