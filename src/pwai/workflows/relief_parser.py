from __future__ import annotations

from dataclasses import dataclass
import re

from .object_resolver import BranchIdentity


@dataclass(frozen=True)
class ContingencyReliefRequest:
    contingency: str
    violated_element: BranchIdentity | None


@dataclass(frozen=True)
class BESSScreenRequest:
    battery_mw: float
    contingency: str | None
    outage: BranchIdentity | None
    monitored: BranchIdentity
    reference_bus: int | None
    requested_mode: str  # DISCHARGE, CHARGE, DISCHARGE_WORSEN, CHARGE_WORSEN, BOTH


def _branch_after_keyword(text: str, keywords: list[str]) -> BranchIdentity | None:
    key = "|".join(re.escape(k) for k in keywords)
    m = re.search(
        rf"(?:{key})\s*(?:branch|line)?\s*(\d+)\s*[-–—]\s*(\d+)"
        rf"(?:\s+(?:circuit|ckt)\s*([A-Za-z0-9&]+))?",
        text,
        re.IGNORECASE,
    )
    if not m:
        return None
    return BranchIdentity(int(m.group(1)), int(m.group(2)), m.group(3) or "1")


def parse_contingency_relief(text: str) -> ContingencyReliefRequest | None:
    ctg = re.search(
        r"(?:contingency|ctg)\s+([A-Za-z0-9_.:\-]+)",
        text,
        re.IGNORECASE,
    )
    branch = _branch_after_keyword(
        text,
        ["on", "violating", "violation", "monitored", "constraint"],
    )

    # Fallback branch: any explicit branch pair.
    if branch is None:
        m = re.search(
            r"(?:branch|line)\s*(\d+)\s*[-–—]\s*(\d+)"
            r"(?:\s+(?:circuit|ckt)\s*([A-Za-z0-9&]+))?",
            text,
            re.IGNORECASE,
        )
        if m:
            branch = BranchIdentity(
                int(m.group(1)), int(m.group(2)), m.group(3) or "1"
            )

    if not ctg and not branch:
        return None
    return ContingencyReliefRequest(
        contingency=ctg.group(1) if ctg else "",
        violated_element=branch,
    )


def parse_bess_screen(text: str) -> BESSScreenRequest | None:
    mw = re.search(
        r"(\d+(?:\.\d+)?)\s*MW\s*(?:battery|bess)?",
        text,
        re.IGNORECASE,
    )
    if not mw:
        # "500 MW battery" and "battery 500 MW" variants
        mw = re.search(
            r"(?:battery|bess).*?(\d+(?:\.\d+)?)\s*MW",
            text,
            re.IGNORECASE,
        )
    if not mw:
        return None

    monitored = _branch_after_keyword(
        text,
        ["on", "monitored", "constraint", "branch", "line"],
    )
    # Better fallback for "on branch 301-501"
    m = re.search(
        r"(?:on|constraint|monitored)\s+(?:branch|line)\s*(\d+)\s*[-–—]\s*(\d+)"
        r"(?:\s+(?:circuit|ckt)\s*([A-Za-z0-9&]+))?",
        text,
        re.IGNORECASE,
    )
    if m:
        monitored = BranchIdentity(
            int(m.group(1)), int(m.group(2)), m.group(3) or "1"
        )
    if monitored is None:
        pairs = list(re.finditer(r"(\d+)\s*[-–—]\s*(\d+)", text))
        if pairs:
            # Last pair is normally the monitored branch when contingency label
            # contains its own bus numbers.
            p = pairs[-1]
            monitored = BranchIdentity(int(p.group(1)), int(p.group(2)), "1")
    if monitored is None:
        return None

    ctg = re.search(
        r"(?:contingency|ctg)\s+([A-Za-z0-9_.:\-]+)",
        text,
        re.IGNORECASE,
    )
    contingency = ctg.group(1) if ctg else None

    outage = None
    if contingency:
        nums = re.findall(r"\d+", contingency)
        if len(nums) >= 2:
            outage = BranchIdentity(
                int(nums[0]), int(nums[1]), nums[2] if len(nums) >= 3 else "1"
            )
    if outage is None:
        outage = _branch_after_keyword(
            text,
            ["outage", "after line", "if line", "when line"],
        )

    ref = re.search(
        r"(?:reference|balancing|slack)\s*(?:bus)?\s*(\d+)",
        text,
        re.IGNORECASE,
    )

    low = text.lower()
    wants_worsen = any(term in low for term in ["worsen", "make worse", "increase congestion"])
    if "discharg" in low:
        requested_mode = "DISCHARGE_WORSEN" if wants_worsen else "DISCHARGE"
    elif "charg" in low:
        requested_mode = "CHARGE_WORSEN" if wants_worsen else "CHARGE"
    else:
        requested_mode = "BOTH"

    return BESSScreenRequest(
        battery_mw=float(mw.group(1)),
        contingency=contingency,
        outage=outage,
        monitored=monitored,
        reference_bus=int(ref.group(1)) if ref else None,
        requested_mode=requested_mode,
    )
