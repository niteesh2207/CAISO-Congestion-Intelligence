from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from time import perf_counter
from typing import Any
import json

import numpy as np

from ..validation.models import (
    BranchResult,
    BusResult,
    GeneratorResult,
    SolveResult,
    SolverIdentity,
)


class ReferenceDCSolver:
    """
    Transparent lossless DC solver used as a reproducible mathematical reference.

    It is not a substitute for AC power flow or PowerWorld. Its purpose is to
    verify topology, branch orientation, source/sink sign conventions, PTDF,
    LODF, outage handling and deterministic cross-solver plumbing.
    """

    def __init__(self) -> None:
        self.case: dict[str, Any] | None = None
        self._source: str | None = None

    @property
    def identity(self) -> SolverIdentity:
        return SolverIdentity(
            name="GridStudio Reference DC",
            version="1.0",
            engine_class="INTERNAL_OPEN_REFERENCE",
            formulation="LOSSLESS_DC_POWER_FLOW",
            open_source=True,
            metadata={
                "purpose": "validation reference, not production AC authority",
                "linear": True,
                "losses": False,
                "reactive_power": False,
            },
        )

    def load_case(self, case_path: str | Path) -> None:
        p = Path(case_path)
        self.case = json.loads(p.read_text(encoding="utf-8"))
        self._source = str(p)
        self._validate()

    def load_dict(self, case: dict[str, Any], source: str = "memory") -> None:
        self.case = deepcopy(case)
        self._source = source
        self._validate()

    def clone(self) -> "ReferenceDCSolver":
        other = ReferenceDCSolver()
        if self.case is not None:
            other.load_dict(self.case, self._source or "clone")
        return other

    def _validate(self) -> None:
        if self.case is None:
            raise RuntimeError("No case loaded.")
        required = {"base_mva", "buses", "branches", "generators", "loads"}
        missing = required - set(self.case)
        if missing:
            raise ValueError(f"Missing case keys: {sorted(missing)}")

        buses = [int(x["bus"]) for x in self.case["buses"]]
        if len(buses) != len(set(buses)):
            raise ValueError("Bus numbers must be unique.")
        if not any(bool(x.get("slack")) for x in self.case["buses"]):
            raise ValueError("Exactly one slack bus is required.")
        if sum(bool(x.get("slack")) for x in self.case["buses"]) != 1:
            raise ValueError("Exactly one slack bus is required.")

        bus_set = set(buses)
        for br in self.case["branches"]:
            if int(br["from_bus"]) not in bus_set or int(br["to_bus"]) not in bus_set:
                raise ValueError("Branch references an unknown bus.")
            if float(br["x_pu"]) == 0:
                raise ValueError("DC branch reactance cannot be zero.")

    def set_branch_status(
        self, from_bus: int, to_bus: int, circuit: str, closed: bool
    ) -> None:
        if self.case is None:
            raise RuntimeError("No case loaded.")
        matches = [
            br for br in self.case["branches"]
            if {int(br["from_bus"]), int(br["to_bus"])}
            == {int(from_bus), int(to_bus)}
            and str(br.get("circuit", "1")) == str(circuit)
        ]
        if len(matches) != 1:
            raise RuntimeError("Branch could not be uniquely resolved.")
        matches[0]["status"] = bool(closed)

    def _injections(self) -> tuple[list[int], np.ndarray, dict[int, float]]:
        assert self.case is not None
        buses = [int(x["bus"]) for x in self.case["buses"]]
        idx = {b: i for i, b in enumerate(buses)}
        p = np.zeros(len(buses), dtype=float)
        for g in self.case["generators"]:
            if bool(g.get("status", True)):
                p[idx[int(g["bus"])]] += float(g["p_mw"])
        for ld in self.case["loads"]:
            if bool(ld.get("status", True)):
                p[idx[int(ld["bus"])]] -= float(ld["p_mw"])
        slack = next(int(x["bus"]) for x in self.case["buses"] if x.get("slack"))
        imbalance = float(p.sum())
        p[idx[slack]] -= imbalance
        return buses, p, {b: float(p[i]) for i, b in enumerate(buses)}

    def _solve(self) -> SolveResult:
        if self.case is None:
            raise RuntimeError("No case loaded.")
        t0 = perf_counter()
        base = float(self.case["base_mva"])
        buses, p_mw, injections = self._injections()
        idx = {b: i for i, b in enumerate(buses)}
        slack = next(int(x["bus"]) for x in self.case["buses"] if x.get("slack"))

        B = np.zeros((len(buses), len(buses)), dtype=float)
        closed = [br for br in self.case["branches"] if bool(br.get("status", True))]
        for br in closed:
            i, j = idx[int(br["from_bus"])], idx[int(br["to_bus"])]
            tap = float(br.get("tap", 1.0))
            b = 1.0 / (float(br["x_pu"]) * tap)
            B[i, i] += b
            B[j, j] += b
            B[i, j] -= b
            B[j, i] -= b

        keep = [i for i, b in enumerate(buses) if b != slack]
        theta = np.zeros(len(buses), dtype=float)
        converged = True
        warnings: list[str] = []
        try:
            theta[keep] = np.linalg.solve(
                B[np.ix_(keep, keep)],
                p_mw[keep] / base,
            )
        except np.linalg.LinAlgError:
            converged = False
            warnings.append("Network is singular or islanded in the DC formulation.")

        branches: list[BranchResult] = []
        for br in self.case["branches"]:
            status = bool(br.get("status", True))
            p = 0.0
            if status and converged:
                i, j = idx[int(br["from_bus"])], idx[int(br["to_bus"])]
                tap = float(br.get("tap", 1.0))
                p = base * (theta[i] - theta[j]) / (
                    float(br["x_pu"]) * tap
                )
            branches.append(BranchResult(
                from_bus=int(br["from_bus"]),
                to_bus=int(br["to_bus"]),
                circuit=str(br.get("circuit", "1")),
                status="Closed" if status else "Open",
                p_from_mw=float(p),
                q_from_mvar=None,
                mva_from=abs(float(p)),
                rating_mva=(
                    float(br["rating_mva"])
                    if br.get("rating_mva") is not None else None
                ),
            ))

        bus_results = [
            BusResult(
                bus=b,
                vm_pu=1.0,
                va_deg=float(np.degrees(theta[idx[b]])),
                p_injection_mw=injections[b],
                q_injection_mvar=None,
            )
            for b in buses
        ]
        gens = [
            GeneratorResult(
                bus=int(g["bus"]),
                gen_id=str(g.get("id", "1")),
                p_mw=float(g["p_mw"]),
                q_mvar=None,
                p_min_mw=float(g.get("p_min_mw", g["p_mw"])),
                p_max_mw=float(g.get("p_max_mw", g["p_mw"])),
                marginal_cost_usd_per_mwh=(
                    float(g["marginal_cost"])
                    if g.get("marginal_cost") is not None else None
                ),
            )
            for g in self.case["generators"]
            if bool(g.get("status", True))
        ]
        return SolveResult(
            solver=self.identity,
            converged=converged,
            elapsed_seconds=perf_counter() - t0,
            buses=bus_results,
            branches=branches,
            generators=gens,
            warnings=warnings,
            raw={"case_source": self._source, "slack_bus": slack},
        )

    def solve_power_flow(self, *, dc: bool = False) -> SolveResult:
        return self._solve()

    def solve_opf(self, *, dc: bool = False) -> SolveResult:
        raise NotImplementedError(
            "The transparent reference engine currently supports DC power flow, "
            "not economic dispatch. Use pandapower, MATPOWER or PowerModels for OPF."
        )

    def ptdf(self, source_bus: int, sink_bus: int) -> list[dict[str, Any]]:
        if self.case is None:
            raise RuntimeError("No case loaded.")
        base = self._solve()
        delta = 1.0
        case = deepcopy(self.case)
        case["generators"].append({
            "bus": int(source_bus), "id": "__PTDF_SOURCE__",
            "p_mw": delta, "status": True,
        })
        case["loads"].append({
            "bus": int(sink_bus), "id": "__PTDF_SINK__",
            "p_mw": delta, "status": True,
        })
        perturbed = ReferenceDCSolver()
        perturbed.load_dict(case, "ptdf-perturbation")
        solved = perturbed.solve_power_flow(dc=True)

        base_map = {
            (x.from_bus, x.to_bus, x.circuit): x for x in base.branches
        }
        rows = []
        for x in solved.branches:
            key = (x.from_bus, x.to_bus, x.circuit)
            b = base_map[key]
            rows.append({
                "from": x.from_bus,
                "to": x.to_bus,
                "circuit": x.circuit,
                "ptdf_pct": 100.0 * (
                    float(x.p_from_mw or 0) - float(b.p_from_mw or 0)
                ) / delta,
            })
        return sorted(rows, key=lambda x: abs(x["ptdf_pct"]), reverse=True)

    def lodf(
        self, outage_from: int, outage_to: int, circuit: str = "1"
    ) -> list[dict[str, Any]]:
        base = self.solve_power_flow(dc=True)
        base_map = {
            (x.from_bus, x.to_bus, x.circuit): x for x in base.branches
        }
        outage_key = next(
            key for key in base_map
            if {key[0], key[1]} == {int(outage_from), int(outage_to)}
            and key[2] == str(circuit)
        )
        outage_flow = float(base_map[outage_key].p_from_mw or 0)
        if abs(outage_flow) < 1e-9:
            raise RuntimeError("Outaged branch base flow is zero; LODF is undefined.")

        post_solver = self.clone()
        post_solver.set_branch_status(outage_from, outage_to, circuit, False)
        post = post_solver.solve_power_flow(dc=True)
        post_map = {
            (x.from_bus, x.to_bus, x.circuit): x for x in post.branches
        }
        rows = []
        for key, b in base_map.items():
            p = post_map[key]
            lodf = (
                -100.0 if key == outage_key
                else 100.0 * (
                    float(p.p_from_mw or 0) - float(b.p_from_mw or 0)
                ) / outage_flow
            )
            rows.append({
                "from": key[0],
                "to": key[1],
                "circuit": key[2],
                "lodf_pct": lodf,
            })
        return sorted(rows, key=lambda x: abs(x["lodf_pct"]), reverse=True)
