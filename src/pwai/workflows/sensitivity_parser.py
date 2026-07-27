from __future__ import annotations
from dataclasses import dataclass
import re
from .object_resolver import BranchIdentity, parse_branch_identity


@dataclass(frozen=True)
class Transfer:
    source_bus: int
    sink_bus: int


@dataclass(frozen=True)
class OTDFRequest:
    transfer: Transfer
    monitored: BranchIdentity
    outage: BranchIdentity


def parse_transfer(text: str) -> Transfer | None:
    patterns = [
        r"(?:from|source)\s*(?:bus\s*)?(\d+)\s*(?:to|sink)\s*(?:bus\s*)?(\d+)",
        r"ptdf\s+(?:from\s+)?(?:bus\s+)?(\d+)\s+(?:to|->|-)\s+(?:bus\s+)?(\d+)",
        r"transfer\s+(?:from\s+)?(?:bus\s+)?(\d+)\s+(?:to|->|-)\s+(?:bus\s+)?(\d+)",
    ]
    for pattern in patterns:
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            return Transfer(int(m.group(1)), int(m.group(2)))
    return None


def parse_monitored_branch(text: str) -> BranchIdentity | None:
    patterns = [
        r"(?:monitor|monitored branch|constraint|on branch)\s*(\d+)\s*[-–—]\s*(\d+)(?:\s+(?:ckt|circuit)\s*([A-Za-z0-9&]+))?",
    ]
    for pattern in patterns:
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            return BranchIdentity(int(m.group(1)), int(m.group(2)), m.group(3) or "1")
    return None


def parse_outage_branch(text: str) -> BranchIdentity | None:
    patterns = [
        r"(?:outage|outage of|if|when)\s+(?:branch|line)?\s*(\d+)\s*[-–—]\s*(\d+)(?:\s+(?:ckt|circuit)\s*([A-Za-z0-9&]+))?\s*(?:trips?|outages?|is out)?",
    ]
    for pattern in patterns:
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            return BranchIdentity(int(m.group(1)), int(m.group(2)), m.group(3) or "1")
    return None
