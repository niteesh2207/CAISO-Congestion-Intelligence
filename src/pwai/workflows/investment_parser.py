from __future__ import annotations

from dataclasses import dataclass
import re

from .object_resolver import BranchIdentity


@dataclass(frozen=True)
class UpgradeRequest:
    branch: BranchIdentity
    delta_mva: float
    source_bus: int | None
    sink_bus: int | None


def parse_upgrade_request(text: str, default_delta_mva: float = 200.0) -> UpgradeRequest | None:
    m = re.search(
        r"(?:branch|line|constraint)\s*(\d+)\s*[-–—]\s*(\d+)"
        r"(?:\s+(?:circuit|ckt)\s*([A-Za-z0-9&]+))?",
        text,
        re.IGNORECASE,
    )
    if not m:
        return None

    delta = re.search(
        r"(?:increase|upgrade|add|raise).*?(\d+(?:\.\d+)?)\s*MVA",
        text,
        re.IGNORECASE,
    )
    spread = re.search(
        r"source\s+bus\s*(\d+).*?sink\s+bus\s*(\d+)",
        text,
        re.IGNORECASE,
    )
    return UpgradeRequest(
        branch=BranchIdentity(
            int(m.group(1)), int(m.group(2)), m.group(3) or "1"
        ),
        delta_mva=float(delta.group(1)) if delta else float(default_delta_mva),
        source_bus=int(spread.group(1)) if spread else None,
        sink_bus=int(spread.group(2)) if spread else None,
    )
