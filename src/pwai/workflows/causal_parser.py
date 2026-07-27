from __future__ import annotations
import re
from dataclasses import dataclass
from .object_resolver import BranchIdentity


@dataclass(frozen=True)
class CausalRequest:
    monitored: BranchIdentity
    outage: BranchIdentity
    reference_bus: int | None = None


def parse_causal_request(text: str) -> CausalRequest | None:
    monitored_patterns = [
        r"(?:monitored branch|monitor|constraint|branch|line)\s*(\d+)\s*[-–—]\s*(\d+)"
        r"(?:\s+(?:circuit|ckt)\s*([A-Za-z0-9&]+))?\s*(?:overload|overloaded|bind|binding|loaded)?",
    ]
    outage_patterns = [
        r"(?:after|when|if|following)\s+(?:outage(?:\s+of)?\s*)?(?:line|branch)?\s*(\d+)\s*[-–—]\s*(\d+)"
        r"(?:\s+(?:circuit|ckt)\s*([A-Za-z0-9&]+))?\s*(?:trips?|is out|outages?)?",
        r"(?:contingency|outage)\s*(?:line|branch)?\s*(\d+)\s*[-–—]\s*(\d+)"
        r"(?:\s+(?:circuit|ckt)\s*([A-Za-z0-9&]+))?",
    ]

    monitored = None
    outage = None

    # Prefer a monitored/constraint-specific phrase.
    for pattern in [
        r"(?:monitored branch|monitor|constraint)\s*(\d+)\s*[-–—]\s*(\d+)(?:\s+(?:circuit|ckt)\s*([A-Za-z0-9&]+))?",
        r"why\s+(?:did|does|is)\s+(?:branch|line)\s*(\d+)\s*[-–—]\s*(\d+)(?:\s+(?:circuit|ckt)\s*([A-Za-z0-9&]+))?",
    ]:
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            monitored = BranchIdentity(int(m.group(1)), int(m.group(2)), m.group(3) or "1")
            break

    for pattern in outage_patterns:
        for m in re.finditer(pattern, text, re.IGNORECASE):
            candidate = BranchIdentity(int(m.group(1)), int(m.group(2)), m.group(3) or "1")
            if monitored and {
                candidate.from_bus, candidate.to_bus
            } == {monitored.from_bus, monitored.to_bus} and candidate.circuit == monitored.circuit:
                continue
            outage = candidate
            break
        if outage:
            break

    # Fallback: collect branch-looking pairs and infer first=monitored, second=outage.
    if not monitored or not outage:
        pairs = list(re.finditer(r"(\d+)\s*[-–—]\s*(\d+)", text))
        if len(pairs) >= 2:
            monitored = monitored or BranchIdentity(int(pairs[0].group(1)), int(pairs[0].group(2)), "1")
            outage = outage or BranchIdentity(int(pairs[1].group(1)), int(pairs[1].group(2)), "1")

    ref = re.search(r"(?:reference|sink)\s*(?:bus)?\s*(\d+)", text, re.IGNORECASE)
    reference_bus = int(ref.group(1)) if ref else None

    if monitored and outage:
        return CausalRequest(monitored=monitored, outage=outage, reference_bus=reference_bus)
    return None
