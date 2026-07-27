from __future__ import annotations

from dataclasses import dataclass
from typing import Any
import re

from .model_doctor import ModelDoctor
from .object_resolver import BranchIdentity, BranchResolver
from .sensitivity import SensitivityEngine


@dataclass(frozen=True)
class BESSCandidate:
    bus: int
    battery_mw: float
    otdf_pct: float
    discharge_effect_mw: float
    charge_effect_mw: float
    discharge_relief_mw: float
    charge_relief_mw: float

    def to_dict(self) -> dict[str, Any]:
        return vars(self)


class BESSIntelligence:
    """
    Static reversible-injection battery placement screen.

    Discharge:
      +MW injection at candidate bus
      balancing withdrawal at reference bus

    Charge:
      -MW injection at candidate bus
      balancing injection at reference bus

    This is a power-only sensitivity study. It does not infer SOC, MWh duration,
    efficiency, cycling constraints, interconnection rights, charging headroom,
    or market dispatch.
    """

    def __init__(self, adapter) -> None:
        self.adapter = adapter
        self.sensitivity = SensitivityEngine(adapter)
        self.doctor = ModelDoctor(adapter)
        self.resolver = BranchResolver(adapter)

    def _branch(self, identity: BranchIdentity) -> dict[str, Any]:
        matches = [
            row for row in self.doctor.branch_snapshot()
            if {int(row["from"]), int(row["to"])} == {identity.from_bus, identity.to_bus}
            and str(row["circuit"]) == str(identity.circuit)
        ]
        if len(matches) != 1:
            raise RuntimeError(
                f"Expected exactly one branch {identity.from_bus}-{identity.to_bus} "
                f"circuit {identity.circuit}; found {len(matches)}."
            )
        return matches[0]

    @staticmethod
    def contingency_to_outage(name: str | None) -> BranchIdentity | None:
        if not name:
            return None
        nums = re.findall(r"\d+", name)
        if len(nums) < 2:
            return None
        return BranchIdentity(
            int(nums[0]), int(nums[1]), nums[2] if len(nums) >= 3 else "1"
        )

    def screen(
        self,
        *,
        battery_mw: float,
        monitored: BranchIdentity,
        outage: BranchIdentity,
        reference_bus: int | None = None,
        top_n: int = 10,
    ) -> dict[str, Any]:
        if battery_mw <= 0:
            raise ValueError("battery_mw must be positive.")

        mon = self._branch(monitored)
        out = self._branch(outage)

        # First-order post-contingency flow used only to determine the overloaded
        # direction for relief-sign interpretation.
        lodf_rows = self.sensitivity.lodf(outage)
        lodf = next(
            (
                float(r["lodf_pct"])
                for r in lodf_rows
                if {int(r["from"]), int(r["to"])} == {monitored.from_bus, monitored.to_bus}
                and str(r["circuit"]) == str(monitored.circuit)
            ),
            None,
        )
        if lodf is None:
            raise RuntimeError("Could not calculate monitored-branch LODF.")

        base_mon_mw = float(mon["mw"])
        outaged_mw = float(out["mw"])
        post_mw = base_mon_mw + lodf / 100.0 * outaged_mw
        flow_sign = 1.0 if post_mw >= 0 else -1.0

        buses = sorted(int(row["bus"]) for row in self.doctor.bus_snapshot())
        ref = int(reference_bus) if reference_bus is not None else min(buses)

        rows = []
        for bus in buses:
            if bus == ref:
                otdf_pct = 0.0
            else:
                result = self.sensitivity.otdf(
                    source_bus=bus,
                    sink_bus=ref,
                    monitored=monitored,
                    outage=outage,
                )
                otdf_pct = float(result["otdf_pct"])

            discharge_effect = battery_mw * otdf_pct / 100.0
            charge_effect = -discharge_effect

            # Positive relief means reduction in absolute post-contingency MW.
            discharge_relief = -flow_sign * discharge_effect
            charge_relief = -flow_sign * charge_effect

            rows.append(BESSCandidate(
                bus=bus,
                battery_mw=battery_mw,
                otdf_pct=otdf_pct,
                discharge_effect_mw=discharge_effect,
                charge_effect_mw=charge_effect,
                discharge_relief_mw=discharge_relief,
                charge_relief_mw=charge_relief,
            ))

        discharge_rank = sorted(
            [r.to_dict() for r in rows],
            key=lambda r: r["discharge_relief_mw"],
            reverse=True,
        )
        charge_rank = sorted(
            [r.to_dict() for r in rows],
            key=lambda r: r["charge_relief_mw"],
            reverse=True,
        )

        return {
            "battery_mw": battery_mw,
            "monitored": {
                "from": monitored.from_bus,
                "to": monitored.to_bus,
                "circuit": monitored.circuit,
            },
            "outage": {
                "from": outage.from_bus,
                "to": outage.to_bus,
                "circuit": outage.circuit,
            },
            "reference_bus": ref,
            "reference_rule": (
                "USER_SELECTED"
                if reference_bus is not None
                else "LOWEST_NUMBERED_BUS_DEMO_OR_FALLBACK_REFERENCE"
            ),
            "base_monitored_mw": base_mon_mw,
            "outaged_line_pre_event_mw": outaged_mw,
            "lodf_pct": lodf,
            "estimated_post_contingency_monitored_mw": post_mw,
            "discharge_best_relief": discharge_rank[:top_n],
            "charge_best_relief": charge_rank[:top_n],
            "discharge_worst": sorted(
                discharge_rank, key=lambda r: r["discharge_relief_mw"]
            )[:top_n],
            "charge_worst": sorted(
                charge_rank, key=lambda r: r["charge_relief_mw"]
            )[:top_n],
            "powerworld_bess_model_note": (
                "PowerWorld recognizes battery energy storage as generator Unit Type BA "
                "and supports energy-storage dynamic models. This V0.10 placement study "
                "does not create a temporary generator/load or alter the case."
            ),
            "guardrails": [
                "STATIC_POWER_ONLY_SCREEN",
                "NO_SOC_OR_MWH_DURATION",
                "NO_EFFICIENCY_OR_CYCLING",
                "NO_INTERCONNECTION_RIGHTS_CHECK",
                "NO_SOLVED_TEMPORARY_BESS_ELEMENT_CREATED",
                "RESULT_DEPENDS_ON_REFERENCE_BUS",
            ],
        }
