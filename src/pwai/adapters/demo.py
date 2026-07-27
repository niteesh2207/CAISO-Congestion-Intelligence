from __future__ import annotations

from copy import deepcopy
from typing import Any

import numpy as np

from .base import PowerWorldAdapter


class DemoAdapter(PowerWorldAdapter):
    """
    Deterministic synthetic demonstration case.

    It exists to develop the product without PowerWorld. Every result must remain
    clearly labeled DEMO and must never be represented as a PowerWorld result.
    """

    def __init__(self) -> None:
        self.connected = False
        self.case_open = False
        self.commands: list[str] = []
        self._saved_states: list[dict[str, Any]] = []
        self.scopf_ctg_violations: list[dict[str, Any]] = []
        self.ctg_injection_sensitivities: list[dict[str, Any]] = []

        self.buses = [
            {"BusNum":101,"BusName":"NORTH","BusPUVolt":1.018,"BusKV":500.0,"BusMWMarginalCost":0.0,"BusMWMarginalCostEnergy":0.0,"BusMWMarginalCostCongestion":0.0,"BusMWMarginalCostLoss":0.0,"x":120,"y":90},
            {"BusNum":201,"BusName":"WEST","BusPUVolt":0.986,"BusKV":230.0,"BusMWMarginalCost":0.0,"BusMWMarginalCostEnergy":0.0,"BusMWMarginalCostCongestion":0.0,"BusMWMarginalCostLoss":0.0,"x":250,"y":170},
            {"BusNum":301,"BusName":"CENTRAL","BusPUVolt":0.941,"BusKV":230.0,"BusMWMarginalCost":0.0,"BusMWMarginalCostEnergy":0.0,"BusMWMarginalCostCongestion":0.0,"BusMWMarginalCostLoss":0.0,"x":390,"y":155},
            {"BusNum":401,"BusName":"EAST","BusPUVolt":0.962,"BusKV":230.0,"BusMWMarginalCost":0.0,"BusMWMarginalCostEnergy":0.0,"BusMWMarginalCostCongestion":0.0,"BusMWMarginalCostLoss":0.0,"x":535,"y":180},
            {"BusNum":501,"BusName":"SOUTH","BusPUVolt":0.918,"BusKV":115.0,"BusMWMarginalCost":0.0,"BusMWMarginalCostEnergy":0.0,"BusMWMarginalCostCongestion":0.0,"BusMWMarginalCostLoss":0.0,"x":390,"y":310},
        ]

        self.branches = [
            {"BusNum":101,"BusNum:1":201,"LineCircuit":"1","LineStatus":"Closed","LineMW":610.0,"LineMVA":628.0,"LineLimMVA":900.0,"LineX":0.10,"LinePTDF":0.0,"LineLODF":0.0,"LineOPFMonitor":"Yes","LineOPFConstraint":"","LineMVAMarginalCost":0.0},
            {"BusNum":101,"BusNum:1":301,"LineCircuit":"1","LineStatus":"Closed","LineMW":780.0,"LineMVA":815.0,"LineLimMVA":800.0,"LineX":0.16,"LinePTDF":0.0,"LineLODF":0.0,"LineOPFMonitor":"Yes","LineOPFConstraint":"","LineMVAMarginalCost":0.0},
            {"BusNum":201,"BusNum:1":301,"LineCircuit":"1","LineStatus":"Closed","LineMW":335.0,"LineMVA":350.0,"LineLimMVA":500.0,"LineX":0.12,"LinePTDF":0.0,"LineLODF":0.0,"LineOPFMonitor":"Yes","LineOPFConstraint":"","LineMVAMarginalCost":0.0},
            {"BusNum":301,"BusNum:1":401,"LineCircuit":"1","LineStatus":"Closed","LineMW":515.0,"LineMVA":541.0,"LineLimMVA":500.0,"LineX":0.11,"LinePTDF":0.0,"LineLODF":0.0,"LineOPFMonitor":"Yes","LineOPFConstraint":"","LineMVAMarginalCost":0.0},
            {"BusNum":301,"BusNum:1":501,"LineCircuit":"1","LineStatus":"Closed","LineMW":270.0,"LineMVA":295.0,"LineLimMVA":350.0,"LineX":0.15,"LinePTDF":0.0,"LineLODF":0.0,"LineOPFMonitor":"Yes","LineOPFConstraint":"","LineMVAMarginalCost":0.0},
            {"BusNum":401,"BusNum:1":501,"LineCircuit":"1","LineStatus":"Closed","LineMW":160.0,"LineMVA":178.0,"LineLimMVA":300.0,"LineX":0.13,"LinePTDF":0.0,"LineLODF":0.0,"LineOPFMonitor":"Yes","LineOPFConstraint":"","LineMVAMarginalCost":0.0},
        ]

        self.gens = [
            {"BusNum":101,"GenID":"1","GenMW":1250.0,"GenMvar":180.0,"GenMWMin":650.0,"GenMWMax":1500.0,"GenStatus":"Closed","GenOPFMWControl":"Yes","GenCostModel":"Piecewise Linear","GenMarginalCost":28.0,"GenCostPerHour":35000.0,"GenDeltaMW":0.0,"GenUnitType":"NG"},
            {"BusNum":201,"GenID":"1","GenMW":420.0,"GenMvar":95.0,"GenMWMin":120.0,"GenMWMax":650.0,"GenStatus":"Closed","GenOPFMWControl":"Yes","GenCostModel":"Piecewise Linear","GenMarginalCost":42.0,"GenCostPerHour":17640.0,"GenDeltaMW":0.0,"GenUnitType":"NG"},
            {"BusNum":401,"GenID":"1","GenMW":310.0,"GenMvar":210.0,"GenMWMin":80.0,"GenMWMax":500.0,"GenStatus":"Closed","GenOPFMWControl":"Yes","GenCostModel":"Piecewise Linear","GenMarginalCost":65.0,"GenCostPerHour":20150.0,"GenDeltaMW":0.0,"GenUnitType":"NG"},
            {"BusNum":501,"GenID":"B1","GenMW":0.0,"GenMvar":0.0,"GenMWMin":-300.0,"GenMWMax":300.0,"GenStatus":"Closed","GenOPFMWControl":"No","GenCostModel":"None","GenMarginalCost":0.0,"GenCostPerHour":0.0,"GenDeltaMW":0.0,"GenUnitType":"BA"},
            {"BusNum":301,"GenID":"B2","GenMW":0.0,"GenMvar":0.0,"GenMWMin":-200.0,"GenMWMax":200.0,"GenStatus":"Closed","GenOPFMWControl":"No","GenCostModel":"None","GenMarginalCost":0.0,"GenCostPerHour":0.0,"GenDeltaMW":0.0,"GenUnitType":"BA"},
        ]

        self.loads = [
            {"BusNum":301,"LoadID":"1","LoadMW":620.0,"LoadMVR":180.0,"LoadStatus":"Closed"},
            {"BusNum":401,"LoadID":"1","LoadMW":490.0,"LoadMVR":150.0,"LoadStatus":"Closed"},
            {"BusNum":501,"LoadID":"1","LoadMW":740.0,"LoadMVR":260.0,"LoadStatus":"Closed"},
        ]

        self._base_gen_mw = {
            (g["BusNum"], str(g["GenID"])): float(g["GenMW"])
            for g in self.gens
        }
        self._base_branch_mw = {
            (b["BusNum"], b["BusNum:1"], str(b["LineCircuit"])): float(b["LineMW"])
            for b in self.branches
        }
        self._base_load_mw = {
            (l["BusNum"], str(l["LoadID"])): float(l["LoadMW"])
            for l in self.loads
        }

    @property
    def solver_backed(self) -> bool:
        return False

    def connect(self) -> None:
        self.connected = True

    def program_information(self) -> dict[str, Any]:
        return {"version":["24","2026-06-29","DEMO"],"addons":["Optimal Power Flow","DEMO","Security-Constrained OPF","DEMO","SimAuto","DEMO","Available Transfer Capability","DEMO","PVQV","DEMO","Transient Stability","DEMO","OPF Reserves","DEMO","Integrated Topology Processing","DEMO"],"executable":["synthetic"]}

    def open_case(self, path: str) -> None:
        if not self.connected:
            raise RuntimeError("Adapter is not connected")
        self.case_open = True

    def close_case(self) -> None:
        self.case_open = False


    def save_state(self) -> None:
        self._saved_states.append(deepcopy({
            "buses": self.buses,
            "branches": self.branches,
            "gens": self.gens,
            "loads": self.loads,
            "scopf_ctg_violations": self.scopf_ctg_violations,
            "ctg_injection_sensitivities": self.ctg_injection_sensitivities,
        }))

    def load_state(self) -> None:
        if not self._saved_states:
            raise RuntimeError("No demo state was saved.")
        state = self._saved_states.pop()
        self.buses = deepcopy(state["buses"])
        self.branches = deepcopy(state["branches"])
        self.gens = deepcopy(state["gens"])
        self.loads = deepcopy(state["loads"])
        self.scopf_ctg_violations = deepcopy(
            state.get("scopf_ctg_violations", [])
        )
        self.ctg_injection_sensitivities = deepcopy(
            state.get("ctg_injection_sensitivities", [])
        )

    def _apply_demo_redispatch(self) -> None:
        """
        Apply net-zero generator redispatch to branch MW using the synthetic
        case's own lossless DC network.

        This exists only for product development. It is not a PowerWorld solve.
        """
        deltas: dict[int, float] = {}
        for g in self.gens:
            key = (g["BusNum"], str(g["GenID"]))
            deltas[g["BusNum"]] = deltas.get(g["BusNum"], 0.0) + (
                float(g["GenMW"]) - self._base_gen_mw[key]
            )

        # Load increases are negative injection changes.
        for ld in self.loads:
            key = (ld["BusNum"], str(ld["LoadID"]))
            load_delta = float(ld["LoadMW"]) - self._base_load_mw[key]
            deltas[ld["BusNum"]] = deltas.get(ld["BusNum"], 0.0) - load_delta

        if not any(abs(v) > 1e-9 for v in deltas.values()):
            return
        if abs(sum(deltas.values())) > 1e-6:
            raise RuntimeError("Demo redispatch is not net-zero.")

        closed = [
            b for b in self.branches
            if str(b["LineStatus"]).lower().startswith("closed")
        ]
        buses = sorted({
            x for b in closed for x in (int(b["BusNum"]), int(b["BusNum:1"]))
        })
        idx = {bus: i for i, bus in enumerate(buses)}
        slack = buses[0]
        B = np.zeros((len(buses), len(buses)), dtype=float)

        for br in closed:
            i, j = idx[int(br["BusNum"])], idx[int(br["BusNum:1"])]
            susceptance = 1.0 / float(br["LineX"])
            B[i, i] += susceptance
            B[j, j] += susceptance
            B[i, j] -= susceptance
            B[j, i] -= susceptance

        non_slack = [b for b in buses if b != slack]
        keep = [idx[b] for b in non_slack]
        rhs = np.array([deltas.get(b, 0.0) for b in non_slack], dtype=float)
        theta_red = np.linalg.solve(B[np.ix_(keep, keep)], rhs)
        theta = {slack: 0.0}
        theta.update({b: float(theta_red[i]) for i, b in enumerate(non_slack)})

        for br in self.branches:
            key = (int(br["BusNum"]), int(br["BusNum:1"]), str(br["LineCircuit"]))
            if not str(br["LineStatus"]).lower().startswith("closed"):
                continue
            delta_flow = (
                theta[int(br["BusNum"])] - theta[int(br["BusNum:1"])]
            ) / float(br["LineX"])
            br["LineMW"] = self._base_branch_mw[key] + delta_flow
            br["LineMVA"] = abs(br["LineMW"]) * 1.045

    def _apply_demo_power_flow(self) -> None:
        out = next(
            (
                b for b in self.branches
                if {b["BusNum"], b["BusNum:1"]} == {301, 401}
                and b["LineCircuit"] == "1"
            ),
            None,
        )
        if not out or not out["LineStatus"].lower().startswith("open"):
            return

        pre_outage_mw = 515.0

        # Synthetic internally consistent outage response for product testing.
        lodf_pct = {
            (101,201,"1"): 0.0,
            (101,301,"1"): 0.0,
            (201,301,"1"): 0.0,
            (301,401,"1"): -100.0,
            (301,501,"1"): 100.0,
            (401,501,"1"): -100.0,
        }

        base_mw = dict(self._base_branch_mw)

        for b in self.branches:
            key = (b["BusNum"], b["BusNum:1"], b["LineCircuit"])
            if key == (301,401,"1"):
                b["LineMW"] = 0.0
                b["LineMVA"] = 0.0
                continue
            delta = (lodf_pct.get(key, 0.0) / 100.0) * pre_outage_mw
            b["LineMW"] = base_mw[key] + delta
            b["LineMVA"] = abs(b["LineMW"]) * 1.045

        vmap = {301:0.928, 401:0.944, 501:0.902}
        for bus in self.buses:
            if bus["BusNum"] in vmap:
                bus["BusPUVolt"] = vmap[bus["BusNum"]]

    def run_script(self, command: str) -> str:
        if not self.case_open:
            raise RuntimeError("No case open")
        self.commands.append(command)

        if "SolvePowerFlow" in command:
            outage_open = any(
                {b["BusNum"], b["BusNum:1"]} == {301, 401}
                and b["LineCircuit"] == "1"
                and str(b["LineStatus"]).lower().startswith("open")
                for b in self.branches
            )
            if outage_open:
                self._apply_demo_power_flow()
            else:
                self._apply_demo_redispatch()

        return "DEMO_OK"

    def get_field_list(self, object_type: str) -> list[dict[str, Any]]:
        mapping = {
            "BUS": self.buses,
            "BRANCH": self.branches,
            "GEN": self.gens,
            "LOAD": self.loads,
            "PWLPOPFCTGVIOL": self.scopf_ctg_violations,
            "VIOLATIONCTGINJECTIONSENSITIVITY": self.ctg_injection_sensitivities,
        }
        rows = mapping.get(object_type.upper(), [])
        if not rows:
            return []

        keysets = {
            "BUS":{"BusNum"},
            "BRANCH":{"BusNum","BusNum:1","LineCircuit"},
            "GEN":{"BusNum","GenID"},
            "LOAD":{"BusNum","LoadID"},
            "PWLPOPFCTGVIOL":{"CTGName","Element"},
            "VIOLATIONCTGINJECTIONSENSITIVITY":{"Injector","Name","Element"},
        }
        return [
            {
                "key_marker":"1" if k in keysets.get(object_type.upper(), set()) else "",
                "variable":k,
                "data_type":"Variant",
                "description":k,
            }
            for k in rows[0].keys()
        ]

    def get_rows(
        self,
        object_type: str,
        fields: list[str],
        filter_text: str = "",
    ) -> list[dict[str, Any]]:
        mapping = {
            "BUS": self.buses,
            "BRANCH": self.branches,
            "GEN": self.gens,
            "LOAD": self.loads,
            "PWLPOPFCTGVIOL": self.scopf_ctg_violations,
            "VIOLATIONCTGINJECTIONSENSITIVITY": self.ctg_injection_sensitivities,
        }
        rows = deepcopy(mapping.get(object_type.upper(), []))
        return [{f: row.get(f) for f in fields} for row in rows]

    def change_single(
        self,
        object_type: str,
        fields: list[str],
        values: list[Any],
    ) -> None:
        change = dict(zip(fields, values))
        obj = object_type.upper()

        if obj == "BRANCH":
            from_bus = int(change.get("BusNum"))
            to_bus = int(change.get("BusNum:1"))
            circuit = str(change.get("LineCircuit", "1"))
            target = next(
                (
                    b for b in self.branches
                    if {b["BusNum"], b["BusNum:1"]} == {from_bus, to_bus}
                    and str(b["LineCircuit"]) == circuit
                ),
                None,
            )
            if target is None:
                raise RuntimeError("Demo branch not found.")
            if "LineStatus" in change:
                target["LineStatus"] = str(change["LineStatus"])
            if "LineLimMVA" in change:
                target["LineLimMVA"] = float(change["LineLimMVA"])
                if float(target["LineLimMVA"]) != 0:
                    target["LinePercent"] = (
                        100.0 * float(target.get("LineMVA", 0.0))
                        / float(target["LineLimMVA"])
                    )
            return

        if obj == "LOAD":
            bus = int(change.get("BusNum"))
            load_id = str(change.get("LoadID", "1"))
            target = next(
                (
                    ld for ld in self.loads
                    if int(ld["BusNum"]) == bus and str(ld["LoadID"]) == load_id
                ),
                None,
            )
            if target is None:
                raise RuntimeError("Demo load not found.")
            if "LoadMW" in change:
                target["LoadMW"] = float(change["LoadMW"])
            return

        if obj == "GEN":
            bus = int(change.get("BusNum"))
            gen_id = str(change.get("GenID", "1"))
            target = next(
                (
                    g for g in self.gens
                    if int(g["BusNum"]) == bus and str(g["GenID"]) == gen_id
                ),
                None,
            )
            if target is None:
                raise RuntimeError("Demo generator not found.")

            if "GenMWMax" in change:
                target["GenMWMax"] = float(change["GenMWMax"])
            if "GenMWMin" in change:
                target["GenMWMin"] = float(change["GenMWMin"])
            if "GenMW" in change:
                value = float(change["GenMW"])
                if (
                    value < float(target["GenMWMin"]) - 1e-9
                    or value > float(target["GenMWMax"]) + 1e-9
                ):
                    raise RuntimeError(
                        "Demo generator MW request violates min/max limits."
                    )
                target["GenMW"] = value
            return

        raise RuntimeError(
            f"Demo mutation does not support {object_type}."
        )
