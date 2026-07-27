from __future__ import annotations

from typing import Any

from .contingency import BranchOutageStudy
from .object_resolver import BranchIdentity


class DifferenceFlowReplay:
    """Build a base → event → post-event visual replay from protected snapshots."""

    def __init__(self, adapter) -> None:
        self.adapter = adapter

    def branch_outage(self, identity: BranchIdentity) -> dict[str, Any]:
        result = BranchOutageStudy(self.adapter).run(identity)

        base_branch = {
            f"{r['from']}-{r['to']}-{r['circuit']}": r
            for r in result.base_branches
        }
        post_branch = {
            f"{r['from']}-{r['to']}-{r['circuit']}": r
            for r in result.post_branches
        }
        base_bus = {int(r["bus"]): r for r in result.base_buses}
        post_bus = {int(r["bus"]): r for r in result.post_buses}

        frames = [
            {
                "frame": 0,
                "label": "BASE",
                "branches": base_branch,
                "buses": base_bus,
            },
            {
                "frame": 1,
                "label": "EVENT",
                "event": result.event,
            },
            {
                "frame": 2,
                "label": "POST_EVENT",
                "branches": post_branch,
                "buses": post_bus,
            },
        ]

        top_thermal = result.thermal_changes[:10]
        top_voltage = result.voltage_changes[:10]

        return {
            "event": result.event,
            "frames": frames,
            "top_thermal_movements": top_thermal,
            "top_voltage_movements": top_voltage,
            "findings": [f.model_dump(mode="json") for f in result.findings],
            "state_restored": True,
            "comparison_mode": "PRODUCT_PROTECTED_DIFFERENCE_REPLAY",
            "powerworld_alignment": (
                "PowerWorld Difference Case compares Present versus Base line flows, "
                "bus voltages and topology. This product creates an auditable protected "
                "scenario replay without changing the user's stored Difference Case base."
            ),
        }
