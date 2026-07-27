from __future__ import annotations
from dataclasses import dataclass
import re
from typing import Any
from .model_doctor import ModelDoctor


@dataclass(frozen=True)
class BranchIdentity:
    from_bus:int
    to_bus:int
    circuit:str="1"


def parse_branch_identity(text:str)->BranchIdentity|None:
    patterns=[
        r"\b(?:line|branch)?\s*(\d+)\s*[-–—]\s*(\d+)(?:\s+(?:circuit|ckt)\s*['\"]?([A-Za-z0-9&]+))?",
        r"\b(?:line|branch)?\s*(\d+)\s+(?:to)\s+(\d+)(?:\s+(?:circuit|ckt)\s*['\"]?([A-Za-z0-9&]+))?",
    ]
    for pattern in patterns:
        match=re.search(pattern,text,re.IGNORECASE)
        if match:
            return BranchIdentity(int(match.group(1)),int(match.group(2)),match.group(3) or "1")
    return None


class BranchResolver:
    def __init__(self,adapter)->None:
        self.adapter=adapter
        self.doctor=ModelDoctor(adapter)

    def resolve(self,identity:BranchIdentity)->dict[str,Any]:
        rows=self.doctor.branch_snapshot()
        matches=[
            row for row in rows
            if {int(row["from"]),int(row["to"])}=={identity.from_bus,identity.to_bus}
            and str(row["circuit"]).strip()==str(identity.circuit).strip()
        ]
        if not matches:
            raise RuntimeError(
                f"Branch {identity.from_bus}-{identity.to_bus} circuit {identity.circuit} was not found."
            )
        if len(matches)>1:
            raise RuntimeError(
                f"Branch identity is ambiguous: {len(matches)} matching records were found."
            )
        return matches[0]
