from __future__ import annotations

from typing import Any

from ..field_catalog import FieldCatalog
from .generator_controls import GeneratorInventory
from .model_doctor import ModelDoctor
from .object_resolver import BranchIdentity
from .sensitivity import SensitivityEngine


class GridHeadroomAnalyzer:
    """
    Transfer-to-limit / distance-to-failure analysis.

    First-order sensitivity gives a fast estimate. A protected stepped AC-power-
    flow verification then identifies the first monitored-branch crossing.
    """

    def __init__(self, adapter) -> None:
        self.adapter = adapter
        self.catalog = FieldCatalog(adapter)
        self.gens = GeneratorInventory(adapter)
        self.doctor = ModelDoctor(adapter)
        self.sensitivity = SensitivityEngine(adapter)

    def _monitored(self, identity: BranchIdentity) -> dict[str, Any]:
        rows = [
            r for r in self.doctor.branch_snapshot()
            if {int(r["from"]), int(r["to"])}
            == {identity.from_bus, identity.to_bus}
            and str(r["circuit"]) == str(identity.circuit)
        ]
        if len(rows) != 1:
            raise RuntimeError("Monitored branch could not be uniquely resolved.")
        return rows[0]

    def _source_gen(self, bus: int):
        candidates = [g for g in self.gens.rows() if g.bus == int(bus)]
        if not candidates:
            raise RuntimeError(f"No generator found at source bus {bus}.")
        candidates.sort(key=lambda g: g.up_headroom_mw, reverse=True)
        return candidates[0]

    def _sink_load(self, bus: int):
        f_bus = self.catalog.choose("LOAD", ["BusNum"])
        f_id = self.catalog.choose("LOAD", ["LoadID", "ID"])
        f_mw = self.catalog.choose("LOAD", ["LoadMW"])
        if not all([f_bus, f_id, f_mw]):
            raise RuntimeError("LOAD Bus/ID/MW fields could not be resolved.")
        rows = [
            r for r in self.adapter.get_rows("LOAD", [f_bus, f_id, f_mw])
            if int(r[f_bus]) == int(bus)
        ]
        if not rows:
            raise RuntimeError(f"No load found at sink bus {bus}.")
        rows.sort(key=lambda r: float(r[f_mw]), reverse=True)
        return {
            "bus_field": f_bus,
            "id_field": f_id,
            "mw_field": f_mw,
            "bus": int(rows[0][f_bus]),
            "id": str(rows[0][f_id]),
            "mw": float(rows[0][f_mw]),
        }

    def transfer_headroom(
        self,
        *,
        source_bus: int,
        sink_bus: int,
        monitored: BranchIdentity,
        step_mw: float = 25.0,
        max_scan_mw: float = 1000.0,
    ) -> dict[str, Any]:
        if step_mw <= 0 or max_scan_mw <= 0:
            raise ValueError("step_mw and max_scan_mw must be positive.")

        base = self._monitored(monitored)
        if base["limit_mva"] in (None, 0) or base["loading_pct"] is None:
            raise RuntimeError("Monitored branch needs a usable MVA limit.")

        ptdf_rows = self.sensitivity.ptdf(source_bus, sink_bus)
        ptdf = next(
            (
                float(r["ptdf_pct"])
                for r in ptdf_rows
                if {int(r["from"]), int(r["to"])}
                == {monitored.from_bus, monitored.to_bus}
                and str(r["circuit"]) == str(monitored.circuit)
            ),
            None,
        )
        if ptdf is None:
            raise RuntimeError("Monitored-branch PTDF was unavailable.")

        mva_margin = max(0.0, float(base["limit_mva"]) - float(base["mva"] or 0))
        first_order_mw = (
            mva_margin / max(abs(ptdf) / 100.0, 1e-9)
            if abs(ptdf) > 1e-9 else None
        )

        source = self._source_gen(source_bus)
        sink = self._sink_load(sink_bus)
        scan_cap = min(
            float(max_scan_mw),
            float(source.up_headroom_mw),
        )

        steps = []
        first_crossing = None

        self.adapter.save_state()
        try:
            delta = 0.0
            while delta <= scan_cap + 1e-9:
                self.gens.set_mw(source, source.mw + delta)
                self.adapter.change_single(
                    "LOAD",
                    [sink["bus_field"], sink["id_field"], sink["mw_field"]],
                    [sink["bus"], sink["id"], sink["mw"] + delta],
                )
                self.adapter.run_script(
                    "EnterMode(PowerFlow); SolvePowerFlow(RECTNEWT);"
                )
                row = self._monitored(monitored)
                point = {
                    "transfer_mw": delta,
                    "loading_pct": row["loading_pct"],
                    "mw": row["mw"],
                    "mva": row["mva"],
                    "limit_mva": row["limit_mva"],
                    "status": (
                        "LIMIT_REACHED"
                        if row["loading_pct"] is not None and row["loading_pct"] >= 100
                        else "SECURE_ON_MONITORED_BRANCH"
                    ),
                }
                steps.append(point)
                if point["status"] == "LIMIT_REACHED":
                    first_crossing = point
                    break
                delta += step_mw
        finally:
            self.adapter.load_state()

        verified_headroom = (
            max(0.0, float(first_crossing["transfer_mw"]) - step_mw)
            if first_crossing else scan_cap
        )

        return {
            "source_bus": source_bus,
            "sink_bus": sink_bus,
            "monitored": vars(monitored),
            "base": base,
            "ptdf_pct": ptdf,
            "first_order_headroom_mw": first_order_mw,
            "verified_secure_transfer_mw": verified_headroom,
            "first_crossing": first_crossing,
            "scan_cap_mw": scan_cap,
            "step_mw": step_mw,
            "steps": steps,
            "state_restored": True,
            "guardrails": [
                "FOCUSED_MONITORED_BRANCH_HEADROOM",
                "SOURCE_GENERATOR_AND_SINK_LOAD_TRANSACTION",
                "OTHER_BASE_VIOLATIONS_DO_NOT_RESET_THIS_FOCUSED_MARGIN",
                "FULL_N1_HEADROOM_REQUIRES_SEPARATE_SECURITY_SCAN",
            ],
        }
