from __future__ import annotations

from typing import Any

from .bess import BESSIntelligence
from .object_resolver import BranchIdentity
from .storage_inventory import StorageInventory


class ExistingBESSRanker:
    def __init__(self, adapter) -> None:
        self.adapter = adapter
        self.inventory = StorageInventory(adapter)
        self.bess = BESSIntelligence(adapter)

    def rank(
        self,
        *,
        action: str,
        duration_hours: float,
        monitored: BranchIdentity,
        outage: BranchIdentity,
        reference_bus: int | None,
    ) -> dict[str, Any]:
        action = action.upper()
        assets = self.inventory.rows(battery_only=True)
        if not assets:
            return {
                "assets": [],
                "results": [],
                "warning": "No existing BA battery units were found in the case.",
            }

        # Get unit-MW sensitivity once per bus using 1 MW.
        screen = self.bess.screen(
            battery_mw=1.0,
            monitored=monitored,
            outage=outage,
            reference_bus=reference_bus,
            top_n=max(20, len(assets) + 5),
        )
        all_rows = {}
        for row in screen["discharge_best_relief"] + screen["discharge_worst"]:
            all_rows[int(row["bus"])] = row

        results = []
        for asset in assets:
            feasibility = asset.feasible_action_mw(action, duration_hours)
            feasible_mw = float(feasibility["feasible_mw"])
            sens = all_rows.get(asset.bus)
            if not sens:
                continue

            per_mw_relief = (
                float(sens["discharge_relief_mw"])
                if action == "DISCHARGE"
                else float(sens["charge_relief_mw"])
            )
            total_relief = feasible_mw * per_mw_relief
            results.append({
                "asset": asset.to_dict(),
                "action": action,
                "duration_hours": duration_hours,
                "feasibility": feasibility,
                "otdf_pct": sens["otdf_pct"],
                "relief_per_mw": per_mw_relief,
                "maximum_feasible_relief_mw": total_relief,
                "projected_soc_at_full_feasible_action_pct": (
                    asset.projected_soc_pct(action, feasible_mw, duration_hours)
                    if feasible_mw > 0 else asset.soc_pct
                ),
            })

        results.sort(
            key=lambda r: r["maximum_feasible_relief_mw"],
            reverse=True,
        )
        return {
            "assets": [a.to_dict() for a in assets],
            "results": results,
            "reference_bus": screen["reference_bus"],
            "monitored": screen["monitored"],
            "outage": screen["outage"],
            "guardrail": (
                "Ranking uses existing BA units only. Energy-limited MW is enforced only when "
                "verified storage metadata is available."
            ),
        }
