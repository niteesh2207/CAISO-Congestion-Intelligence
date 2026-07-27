from __future__ import annotations

from dataclasses import dataclass
import re

from .object_resolver import BranchIdentity


@dataclass(frozen=True)
class BESSActionRequest:
    bus: int
    gen_id: str
    action: str
    mw: float
    duration_hours: float
    balancing_bus: int | None
    balancing_gen_id: str | None
    contingency: str | None
    monitored: BranchIdentity | None
    source_bus: int | None
    sink_bus: int | None


def _branch(text: str) -> BranchIdentity | None:
    m = re.search(
        r"(?:monitored\s+)?(?:branch|line|constraint)\s*(\d+)\s*[-–—]\s*(\d+)"
        r"(?:\s+(?:circuit|ckt)\s*([A-Za-z0-9&]+))?",
        text,
        re.IGNORECASE,
    )
    if not m:
        return None
    return BranchIdentity(int(m.group(1)), int(m.group(2)), m.group(3) or "1")


def parse_bess_action(text: str) -> BESSActionRequest | None:
    asset = re.search(
        r"(?:battery|bess)\s+(?:at\s+)?(?:bus\s*)?(\d+)"
        r"(?:\s*[/,:]\s*([A-Za-z0-9&]+)|\s+(?:id|gen)\s+([A-Za-z0-9&]+))?",
        text,
        re.IGNORECASE,
    )
    if not asset:
        return None

    low = text.lower()
    if "discharg" in low:
        action = "DISCHARGE"
    elif "charg" in low:
        action = "CHARGE"
    else:
        return None

    mw = re.search(r"(\d+(?:\.\d+)?)\s*MW", text, re.IGNORECASE)
    if not mw:
        return None

    duration = re.search(
        r"(?:for|duration)\s*(\d+(?:\.\d+)?)\s*(?:h|hr|hrs|hour|hours)\b",
        text,
        re.IGNORECASE,
    )

    balance = re.search(
        r"(?:balance|balancing)\s+(?:generator|gen)\s+"
        r"(?:bus\s*)?(\d+)(?:\s*[/,:]\s*([A-Za-z0-9&]+))?",
        text,
        re.IGNORECASE,
    )

    ctg = re.search(
        r"(?:contingency|ctg)\s+([A-Za-z0-9_.:\-]+)",
        text,
        re.IGNORECASE,
    )

    spread = re.search(
        r"(?:source\s+bus\s*(\d+)).*?(?:sink\s+bus\s*(\d+))",
        text,
        re.IGNORECASE,
    )

    return BESSActionRequest(
        bus=int(asset.group(1)),
        gen_id=str(asset.group(2) or asset.group(3) or "1"),
        action=action,
        mw=float(mw.group(1)),
        duration_hours=float(duration.group(1)) if duration else 1.0,
        balancing_bus=int(balance.group(1)) if balance else None,
        balancing_gen_id=str(balance.group(2) or "1") if balance else None,
        contingency=ctg.group(1) if ctg else None,
        monitored=_branch(text),
        source_bus=int(spread.group(1)) if spread else None,
        sink_bus=int(spread.group(2)) if spread else None,
    )
