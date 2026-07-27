from __future__ import annotations
import re
from dataclasses import dataclass
from .object_resolver import BranchIdentity


@dataclass(frozen=True)
class RemedyRequest:
    monitored: BranchIdentity
    reference_bus: int | None
    target_loading_pct: float = 98.0


def parse_remedy_request(text: str) -> RemedyRequest | None:
    m = re.search(
        r"(?:monitored\s+)?(?:branch|line|constraint)\s*(\d+)\s*[-–—]\s*(\d+)"
        r"(?:\s+(?:circuit|ckt)\s*([A-Za-z0-9&]+))?",
        text,
        re.IGNORECASE,
    )
    if not m:
        return None

    ref = re.search(r"(?:reference|sink)\s*(?:bus)?\s*(\d+)", text, re.IGNORECASE)
    target = re.search(
        r"(?:target|to|below)\s*(\d+(?:\.\d+)?)\s*%",
        text,
        re.IGNORECASE,
    )

    return RemedyRequest(
        monitored=BranchIdentity(int(m.group(1)), int(m.group(2)), m.group(3) or "1"),
        reference_bus=int(ref.group(1)) if ref else None,
        target_loading_pct=float(target.group(1)) if target else 98.0,
    )
