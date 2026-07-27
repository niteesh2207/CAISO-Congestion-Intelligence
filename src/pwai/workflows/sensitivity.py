from __future__ import annotations
from typing import Any
from ..field_catalog import FieldCatalog
from .dc_sensitivity import DCBranch, DCSensitivityModel
from .model_doctor import ModelDoctor
from .object_resolver import BranchIdentity, BranchResolver


def _branch_key(row: dict[str, Any]) -> tuple[int, int, str]:
    a, b = int(row["from"]), int(row["to"])
    return (a, b, str(row["circuit"]))


def _same_branch(row: dict[str, Any], identity: BranchIdentity) -> bool:
    return (
        {int(row["from"]), int(row["to"])} == {identity.from_bus, identity.to_bus}
        and str(row["circuit"]).strip() == str(identity.circuit).strip()
    )


class SensitivityEngine:
    def __init__(self, adapter) -> None:
        self.adapter = adapter
        self.catalog = FieldCatalog(adapter)
        self.doctor = ModelDoctor(adapter)
        self.resolver = BranchResolver(adapter)

    def _demo_dc(self) -> DCSensitivityModel:
        rows = self.adapter.get_rows(
            "BRANCH",
            ["BusNum", "BusNum:1", "LineCircuit", "LineX", "LineStatus"],
        )
        branches = [
            DCBranch(int(r["BusNum"]), int(r["BusNum:1"]), str(r["LineCircuit"]), float(r["LineX"]))
            for r in rows
            if str(r.get("LineStatus", "Closed")).lower().startswith("closed")
        ]
        buses = sorted({
            x for br in branches for x in [br.from_bus, br.to_bus]
        })
        return DCSensitivityModel(buses, branches, slack_bus=buses[0])

    def _branch_identity_fields(self) -> tuple[str, str, str]:
        return (
            self.catalog.choose("BRANCH", ["BusNum", "BusNumFrom"]) or "",
            self.catalog.choose("BRANCH", ["BusNum:1", "BusNumTo"]) or "",
            self.catalog.choose("BRANCH", ["LineCircuit", "Circuit"]) or "",
        )

    def _find_ptdf_field(self) -> str:
        field = self.catalog.choose("BRANCH", ["LinePTDF", "PTDF"])
        if field:
            return field
        semantic = self.catalog.find_semantic(
            "BRANCH", include=["ptdf"], exclude=["multiple", "abs"]
        )
        if semantic:
            return semantic
        raise RuntimeError("Could not resolve a signed branch PTDF field from PowerWorld.")

    def _find_lodf_field(self) -> str:
        field = self.catalog.choose("BRANCH", ["LineLODF", "LODF"])
        if field:
            return field
        semantic = self.catalog.find_semantic(
            "BRANCH", include=["lodf"], exclude=["multiple", "matrix"]
        )
        if semantic:
            return semantic
        raise RuntimeError("Could not resolve a branch LODF field from PowerWorld.")

    def ptdf(self, source_bus: int, sink_bus: int, method: str = "DC") -> list[dict[str, Any]]:
        if source_bus == sink_bus:
            raise ValueError("PTDF source and sink must differ.")

        if not self.adapter.solver_backed:
            rows = self._demo_dc().ptdf(source_bus, sink_bus)
            rows.sort(key=lambda r: abs(r["ptdf_pct"]), reverse=True)
            return rows

        self.adapter.run_script("EnterMode(Run);")
        self.adapter.run_script(
            f"CalculatePTDF([BUS {int(source_bus)}], [BUS {int(sink_bus)}], {method});"
        )

        f_from, f_to, f_ckt = self._branch_identity_fields()
        ptdf_field = self._find_ptdf_field()
        if not all([f_from, f_to, f_ckt]):
            raise RuntimeError("Could not resolve branch identity fields.")

        raw = self.adapter.get_rows("BRANCH", [f_from, f_to, f_ckt, ptdf_field])
        rows = []
        for r in raw:
            try:
                val = float(r[ptdf_field])
            except (TypeError, ValueError):
                continue
            rows.append({
                "from": int(r[f_from]),
                "to": int(r[f_to]),
                "circuit": str(r[f_ckt]),
                "ptdf_pct": val,
            })
        rows.sort(key=lambda r: abs(r["ptdf_pct"]), reverse=True)
        return rows

    def lodf(self, outage: BranchIdentity, method: str = "DC") -> list[dict[str, Any]]:
        target = self.resolver.resolve(outage)

        if not self.adapter.solver_backed:
            model = self._demo_dc()
            branch = next(
                br for br in model.branches
                if {br.from_bus, br.to_bus} == {outage.from_bus, outage.to_bus}
                and br.circuit == outage.circuit
            )
            rows = model.lodf(branch)
            rows.sort(key=lambda r: abs(r["lodf_pct"]), reverse=True)
            return rows

        self.adapter.run_script("EnterMode(Run);")
        self.adapter.run_script(
            f"CalculateLODF([BRANCH {int(target['from'])} {int(target['to'])} '{target['circuit']}'], {method});"
        )

        f_from, f_to, f_ckt = self._branch_identity_fields()
        lodf_field = self._find_lodf_field()
        raw = self.adapter.get_rows("BRANCH", [f_from, f_to, f_ckt, lodf_field])
        rows = []
        for r in raw:
            try:
                val = float(r[lodf_field])
            except (TypeError, ValueError):
                continue
            rows.append({
                "from": int(r[f_from]),
                "to": int(r[f_to]),
                "circuit": str(r[f_ckt]),
                "lodf_pct": val,
            })
        rows.sort(key=lambda r: abs(r["lodf_pct"]), reverse=True)
        return rows

    def otdf(
        self,
        *,
        source_bus: int,
        sink_bus: int,
        monitored: BranchIdentity,
        outage: BranchIdentity,
        method: str = "DC",
    ) -> dict[str, Any]:
        ptdfs = self.ptdf(source_bus, sink_bus, method)
        lodfs = self.lodf(outage, method)

        ptdf_mon = next((r["ptdf_pct"] for r in ptdfs if _same_branch(r, monitored)), None)
        ptdf_out = next((r["ptdf_pct"] for r in ptdfs if _same_branch(r, outage)), None)
        lodf_mon = next((r["lodf_pct"] for r in lodfs if _same_branch(r, monitored)), None)

        if None in (ptdf_mon, ptdf_out, lodf_mon):
            raise RuntimeError("Could not resolve all PTDF/LODF terms needed for OTDF.")

        otdf_pct = ptdf_mon + (lodf_mon / 100.0) * ptdf_out
        return {
            "source_bus": source_bus,
            "sink_bus": sink_bus,
            "monitored": {
                "from": monitored.from_bus, "to": monitored.to_bus, "circuit": monitored.circuit
            },
            "outage": {
                "from": outage.from_bus, "to": outage.to_bus, "circuit": outage.circuit
            },
            "ptdf_monitored_pct": ptdf_mon,
            "ptdf_outage_pct": ptdf_out,
            "lodf_monitored_for_outage_pct": lodf_mon,
            "otdf_pct": otdf_pct,
            "formula": "OTDFx = PTDFx + LODFx,y * PTDFy",
        }

    def bus_shift_screen(
        self,
        monitored: BranchIdentity,
        *,
        sink_bus: int,
        top_n: int = 10,
    ) -> dict[str, list[dict[str, Any]]]:
        """
        Rank each bus as source against a fixed sink.

        In real PowerWorld mode this intentionally uses repeated signed PTDF
        calculations. It is slower than the native Shift Factor tool but avoids
        relying on undocumented result-field assumptions. Native CalculateShiftFactors
        integration is a future optimization.
        """
        bus_num = self.catalog.choose("BUS", ["BusNum"])
        if not bus_num:
            raise RuntimeError("Could not resolve BusNum.")
        buses = [
            int(r[bus_num]) for r in self.adapter.get_rows("BUS", [bus_num])
            if r.get(bus_num) is not None
        ]

        scored = []
        for source in buses:
            if source == sink_bus:
                continue
            rows = self.ptdf(source, sink_bus, "DC")
            val = next((r["ptdf_pct"] for r in rows if _same_branch(r, monitored)), None)
            if val is not None:
                scored.append({"source_bus": source, "sink_bus": sink_bus, "shift_factor_pct": val})

        worsen = sorted(scored, key=lambda r: r["shift_factor_pct"], reverse=True)[:top_n]
        relieve = sorted(scored, key=lambda r: r["shift_factor_pct"])[:top_n]
        return {"worsen": worsen, "relieve": relieve}
