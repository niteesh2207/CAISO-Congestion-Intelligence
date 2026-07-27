from __future__ import annotations

from typing import Any

from .constraint_economics import ConstraintEconomics
from .model_doctor import ModelDoctor
from .native_contingency import NativeContingencyEngine
from .object_resolver import BranchIdentity
from .optimization import OptimizationIntelligence


class TransmissionUpgradeStudy:
    """
    Protected branch-rating upgrade study.

    The tool changes only the resolved branch MVA limit, runs physical/security/
    economic comparisons, then restores the original case. It does not infer
    construction feasibility or capital cost.
    """

    def __init__(self, adapter) -> None:
        self.adapter = adapter
        self.doctor = ModelDoctor(adapter)

    def _find(self, identity: BranchIdentity) -> dict[str, Any]:
        rows = [
            r for r in self.doctor.branch_snapshot()
            if {int(r["from"]), int(r["to"])} == {identity.from_bus, identity.to_bus}
            and str(r["circuit"]) == str(identity.circuit)
        ]
        if len(rows) != 1:
            raise RuntimeError(
                f"Expected one branch {identity.from_bus}-{identity.to_bus} "
                f"{identity.circuit}; found {len(rows)}."
            )
        return rows[0]

    def _set_limit(self, identity: BranchIdentity, value: float) -> None:
        f = self.doctor.branch_fields()
        if not f["limit"]:
            raise RuntimeError("Could not resolve branch MVA limit field.")
        self.adapter.change_single(
            "BRANCH",
            [f["from"], f["to"], f["circuit"], f["limit"]],
            [identity.from_bus, identity.to_bus, identity.circuit, float(value)],
        )

    @staticmethod
    def _cost(result: dict[str, Any] | None) -> float | None:
        if not result:
            return None
        return result.get("after", {}).get("total_generation_cost_per_hour")

    def run(
        self,
        *,
        branch: BranchIdentity,
        delta_mva: float,
        source_bus: int | None = None,
        sink_bus: int | None = None,
    ) -> dict[str, Any]:
        if delta_mva <= 0:
            raise ValueError("delta_mva must be positive.")

        base_branch = self._find(branch)
        base_limit = base_branch.get("limit_mva")
        if base_limit in (None, 0):
            raise RuntimeError("Branch has no usable MVA limit.")
        new_limit = float(base_limit) + float(delta_mva)

        baseline_n1 = NativeContingencyEngine(self.adapter).run_all()

        baseline_opf = baseline_scopf = None
        self.adapter.save_state()
        try:
            baseline_opf = OptimizationIntelligence(self.adapter).run("OPF").to_dict()
        finally:
            self.adapter.load_state()

        self.adapter.save_state()
        try:
            baseline_scopf = OptimizationIntelligence(self.adapter).run("SCOPF").to_dict()
        except Exception:
            baseline_scopf = None
        finally:
            self.adapter.load_state()

        baseline_econ = None
        self.adapter.save_state()
        try:
            OptimizationIntelligence(self.adapter).run("OPF")
            e = ConstraintEconomics(self.adapter)
            s = e.snapshot()
            baseline_econ = {
                "snapshot": s.to_dict(),
                "spread": (
                    e.spread(s, source_bus=source_bus, sink_bus=sink_bus)
                    if source_bus is not None and sink_bus is not None else None
                ),
            }
        finally:
            self.adapter.load_state()

        self.adapter.save_state()
        try:
            self._set_limit(branch, new_limit)
            self.adapter.run_script("EnterMode(PowerFlow); SolvePowerFlow(RECTNEWT);")
            post_branch = self._find(branch)
            candidate_n1 = NativeContingencyEngine(self.adapter).run_all()

            candidate_opf = OptimizationIntelligence(self.adapter).run("OPF").to_dict()
            econ = ConstraintEconomics(self.adapter)
            snap = econ.snapshot()
            candidate_econ = {
                "snapshot": snap.to_dict(),
                "spread": (
                    econ.spread(snap, source_bus=source_bus, sink_bus=sink_bus)
                    if source_bus is not None and sink_bus is not None else None
                ),
            }

            self._set_limit(branch, new_limit)
            self.adapter.run_script("EnterMode(PowerFlow); SolvePowerFlow(RECTNEWT);")
            try:
                candidate_scopf = OptimizationIntelligence(self.adapter).run("SCOPF").to_dict()
            except Exception as exc:
                candidate_scopf = {"error": str(exc)}
        finally:
            self.adapter.load_state()

        def delta(a, b):
            return b-a if a is not None and b is not None else None

        base_cost_opf = self._cost(baseline_opf)
        cand_cost_opf = self._cost(candidate_opf)
        base_cost_scopf = self._cost(baseline_scopf)
        cand_cost_scopf = (
            self._cost(candidate_scopf)
            if isinstance(candidate_scopf, dict) and "error" not in candidate_scopf else None
        )

        base_spread = (
            baseline_econ.get("spread", {}).get("total_spread_per_mwh")
            if baseline_econ and baseline_econ.get("spread") else None
        )
        cand_spread = (
            candidate_econ.get("spread", {}).get("total_spread_per_mwh")
            if candidate_econ and candidate_econ.get("spread") else None
        )

        return {
            "branch": vars(branch),
            "base_limit_mva": base_limit,
            "upgrade_delta_mva": delta_mva,
            "new_limit_mva": new_limit,
            "base_branch": base_branch,
            "post_upgrade_branch": post_branch,
            "n1": {
                "baseline": baseline_n1.to_dict(),
                "candidate": candidate_n1.to_dict(),
                "violation_delta": len(candidate_n1.violations)-len(baseline_n1.violations),
                "unsolved_delta": candidate_n1.unsolved_count-baseline_n1.unsolved_count,
            },
            "opf": {
                "baseline_cost_per_hour": base_cost_opf,
                "candidate_cost_per_hour": cand_cost_opf,
                "cost_delta_per_hour": delta(base_cost_opf, cand_cost_opf),
            },
            "scopf": {
                "baseline_cost_per_hour": base_cost_scopf,
                "candidate_cost_per_hour": cand_cost_scopf,
                "cost_delta_per_hour": delta(base_cost_scopf, cand_cost_scopf),
            },
            "lmp_spread": {
                "baseline_per_mwh": base_spread,
                "candidate_per_mwh": cand_spread,
                "delta_per_mwh": delta(base_spread, cand_spread),
            },
            "baseline_economics": baseline_econ,
            "candidate_economics": candidate_econ,
            "state_restored": True,
            "guardrails": [
                "RATING_CHANGE_ONLY",
                "NO_CONSTRUCTION_FEASIBILITY_INFERENCE",
                "NO_CAPEX_INFERENCE",
                "PROTECTED_STATE_RESTORE",
            ],
        }
