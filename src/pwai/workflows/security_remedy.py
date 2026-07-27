from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .native_contingency import NativeContingencyEngine, ContingencyBatchResult
from .object_resolver import BranchIdentity
from .remedy import RemedyIntelligence, RedispatchCandidate
from .security_compare import compare_security


@dataclass
class SecurityRemedyResult:
    monitored: dict[str, Any]
    reference_bus: int
    target_loading_pct: float
    baseline_n1: dict[str, Any]
    base: dict[str, Any]
    screening: list[dict[str, Any]]
    tested: list[dict[str, Any]]
    recommended: dict[str, Any] | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "monitored": self.monitored,
            "reference_bus": self.reference_bus,
            "target_loading_pct": self.target_loading_pct,
            "baseline_n1": self.baseline_n1,
            "base": self.base,
            "screening": self.screening,
            "tested": self.tested,
            "recommended": self.recommended,
        }


class SecurityConstrainedRemedy:
    """
    V0.6 extension of balanced redispatch.

    Every candidate must:
    1. pass the V0.5 base-case screen;
    2. be compared against the baseline contingency result set;
    3. create no new unsolved contingencies;
    4. create no new contingency violations;
    5. avoid material worsening of existing contingency violations.
    """

    def __init__(self, adapter) -> None:
        self.adapter = adapter
        self.remedy = RemedyIntelligence(adapter)
        self.ctg = NativeContingencyEngine(adapter)

    def baseline(self) -> ContingencyBatchResult:
        return self.ctg.run_all()

    def test_candidate_n1(
        self,
        monitored: BranchIdentity,
        candidate: RedispatchCandidate,
        *,
        target_loading_pct: float,
        baseline_n1: ContingencyBatchResult,
    ) -> dict[str, Any]:
        base_branches = self.remedy.doctor.branch_snapshot()
        base_buses = self.remedy.doctor.bus_snapshot()
        base_mon = self.remedy._branch(base_branches, monitored)

        delta = candidate.feasible_redispatch_mw
        donor_target = candidate.donor.mw - delta
        receiver_target = candidate.receiver.mw + delta

        self.adapter.save_state()
        try:
            self.remedy.generators.set_mw(candidate.donor, donor_target)
            self.remedy.generators.set_mw(candidate.receiver, receiver_target)
            self.adapter.run_script("EnterMode(PowerFlow); SolvePowerFlow(RECTNEWT);")

            actual_donor = self.remedy.generators.read_mw(
                candidate.donor.bus, candidate.donor.gen_id
            )
            actual_receiver = self.remedy.generators.read_mw(
                candidate.receiver.bus, candidate.receiver.gen_id
            )
            post_branches = self.remedy.doctor.branch_snapshot()
            post_buses = self.remedy.doctor.bus_snapshot()
            post_mon = self.remedy._branch(post_branches, monitored)

            drift = False
            if actual_donor is not None and abs(actual_donor - donor_target) > 1.0:
                drift = True
            if actual_receiver is not None and abs(actual_receiver - receiver_target) > 1.0:
                drift = True

            secondary = self.remedy._secondary_violations(
                base_branches, post_branches, base_buses, post_buses, monitored
            )

            candidate_n1 = self.ctg.run_all()
            comparison = compare_security(baseline_n1, candidate_n1)
        finally:
            self.adapter.load_state()

        base_pct = float(base_mon["loading_pct"])
        post_pct = float(post_mon["loading_pct"])

        base_security_pass = not secondary and not drift
        n1_security_pass = comparison.pass_security
        overall_pass = base_security_pass and n1_security_pass

        return {
            "donor": {
                "bus": candidate.donor.bus,
                "id": candidate.donor.gen_id,
                "base_mw": candidate.donor.mw,
                "target_mw": donor_target,
                "actual_mw_after_solve": actual_donor,
                "shift_factor_pct": candidate.donor_shift_pct,
            },
            "receiver": {
                "bus": candidate.receiver.bus,
                "id": candidate.receiver.gen_id,
                "base_mw": candidate.receiver.mw,
                "target_mw": receiver_target,
                "actual_mw_after_solve": actual_receiver,
                "shift_factor_pct": candidate.receiver_shift_pct,
            },
            "redispatch_mw_each_direction": delta,
            "predicted_relief_mw": candidate.predicted_relief_mw,
            "base_loading_pct": base_pct,
            "post_loading_pct": post_pct,
            "target_loading_pct": target_loading_pct,
            "target_met": post_pct <= target_loading_pct + 1e-6,
            "control_drift": drift,
            "base_secondary_violations": secondary,
            "base_security_pass": base_security_pass,
            "n1_security": comparison.to_dict(),
            "n1_security_pass": n1_security_pass,
            "overall_security_pass": overall_pass,
            "state_restored": True,
        }

    def run(
        self,
        monitored: BranchIdentity,
        *,
        reference_bus: int | None,
        target_loading_pct: float,
        max_tested: int = 8,
    ) -> SecurityRemedyResult:
        baseline_n1 = self.baseline()
        base, resolved_reference, candidates = self.remedy.screen(
            monitored,
            reference_bus=reference_bus,
            target_loading_pct=target_loading_pct,
            max_candidates=max_tested,
        )

        screening = [{
            "donor_bus": c.donor.bus,
            "donor_id": c.donor.gen_id,
            "receiver_bus": c.receiver.bus,
            "receiver_id": c.receiver.gen_id,
            "required_redispatch_mw": c.required_redispatch_mw,
            "feasible_redispatch_mw": c.feasible_redispatch_mw,
            "predicted_relief_mw": c.predicted_relief_mw,
            "predicted_to_target": c.predicted_to_target,
        } for c in candidates]

        tested = [
            self.test_candidate_n1(
                monitored,
                c,
                target_loading_pct=target_loading_pct,
                baseline_n1=baseline_n1,
            )
            for c in candidates[:max_tested]
        ]

        tested.sort(key=lambda r: (
            not r["overall_security_pass"],
            not r["target_met"],
            r["redispatch_mw_each_direction"],
            r["post_loading_pct"],
        ))

        recommended = next(
            (
                row for row in tested
                if row["overall_security_pass"] and row["target_met"]
            ),
            None,
        )
        if recommended is None:
            recommended = next(
                (row for row in tested if row["overall_security_pass"]),
                None,
            )

        return SecurityRemedyResult(
            monitored={
                "from": monitored.from_bus,
                "to": monitored.to_bus,
                "circuit": monitored.circuit,
            },
            reference_bus=resolved_reference,
            target_loading_pct=target_loading_pct,
            baseline_n1=baseline_n1.to_dict(),
            base=base,
            screening=screening,
            tested=tested,
            recommended=recommended,
        )
