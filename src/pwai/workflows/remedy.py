from __future__ import annotations
from dataclasses import dataclass
from typing import Any
import math

from ..models import Finding
from .generator_controls import GeneratorControl, GeneratorInventory
from .model_doctor import ModelDoctor
from .object_resolver import BranchIdentity, BranchResolver
from .sensitivity import SensitivityEngine


@dataclass(frozen=True)
class RedispatchCandidate:
    donor: GeneratorControl
    receiver: GeneratorControl
    donor_shift_pct: float
    receiver_shift_pct: float
    relief_per_mw: float
    required_redispatch_mw: float
    feasible_redispatch_mw: float
    predicted_relief_mw: float
    predicted_to_target: bool


@dataclass
class RemedySearchResult:
    monitored: dict[str, Any]
    reference_bus: int
    target_loading_pct: float
    base: dict[str, Any]
    screening: list[dict[str, Any]]
    tested: list[dict[str, Any]]
    recommended: dict[str, Any] | None
    findings: list[Finding]


def _same_branch(row: dict[str, Any], identity: BranchIdentity) -> bool:
    return (
        {int(row["from"]), int(row["to"])} == {identity.from_bus, identity.to_bus}
        and str(row["circuit"]).strip() == str(identity.circuit).strip()
    )


class RemedyIntelligence:
    """
    V1 remedy search: balanced two-generator redispatch.

    Screening:
      donor generator decreases MW
      receiver generator increases MW by the same amount

    This preserves net generation to first order instead of asking the slack bus
    to absorb the entire change.

    Candidate screening uses signed PTDFs against one common reference bus:
      pair_effect = SF_receiver - SF_donor

    For a positive-flow monitored branch, relief requires pair_effect < 0.
    For a negative-flow branch, relief requires pair_effect > 0.

    Final ranking is based on protected solved scenarios, not the linear estimate.
    """

    def __init__(self, adapter) -> None:
        self.adapter = adapter
        self.doctor = ModelDoctor(adapter)
        self.sensitivity = SensitivityEngine(adapter)
        self.generators = GeneratorInventory(adapter)
        self.resolver = BranchResolver(adapter)

    def _branch(self, rows: list[dict[str, Any]], identity: BranchIdentity) -> dict[str, Any]:
        matches = [r for r in rows if _same_branch(r, identity)]
        if len(matches) != 1:
            raise RuntimeError(
                f"Expected one monitored branch; found {len(matches)}."
            )
        return matches[0]

    def _required_abs_mw_relief(
        self, branch: dict[str, Any], target_loading_pct: float
    ) -> float:
        mw = branch.get("mw")
        mva = branch.get("mva")
        limit = branch.get("limit_mva")
        if mw is None or mva is None or limit in (None, 0):
            raise RuntimeError(
                "Remedy screening needs monitored MW, MVA and MVA limit."
            )
        mw = abs(float(mw))
        mva = abs(float(mva))
        limit = float(limit)
        if mva < 1e-9:
            return 0.0

        target_mva = limit * target_loading_pct / 100.0
        if mva <= target_mva:
            return 0.0

        # Preserve the present |MW|/MVA ratio as a first-pass linear conversion.
        # The protected AC solve is the final authority.
        target_abs_mw = target_mva * (mw / mva)
        return max(0.0, mw - target_abs_mw)

    def screen(
        self,
        monitored: BranchIdentity,
        *,
        reference_bus: int | None = None,
        target_loading_pct: float = 98.0,
        max_candidates: int = 12,
    ) -> tuple[dict[str, Any], int, list[RedispatchCandidate]]:
        mon = self.resolver.resolve(monitored)
        ref = int(reference_bus) if reference_bus is not None else int(mon["to"])

        base = self._branch(self.doctor.branch_snapshot(), monitored)
        current_mw = float(base["mw"])
        flow_sign = 1.0 if current_mw >= 0 else -1.0
        required_abs_relief = self._required_abs_mw_relief(base, target_loading_pct)

        gens = self.generators.rows()
        if len(gens) < 2:
            raise RuntimeError("At least two online generators are required.")

        # One common reference makes pair differences reference-invariant.
        shift_by_bus: dict[int, float] = {ref: 0.0}
        for bus in sorted({g.bus for g in gens}):
            if bus == ref:
                continue
            rows = self.sensitivity.ptdf(bus, ref, "DC")
            value = next(
                (r["ptdf_pct"] for r in rows if _same_branch(r, monitored)),
                None,
            )
            if value is not None:
                shift_by_bus[bus] = float(value)

        candidates: list[RedispatchCandidate] = []
        for donor in gens:
            donor_sf = shift_by_bus.get(donor.bus)
            if donor_sf is None or donor.down_headroom_mw <= 0:
                continue

            for receiver in gens:
                if receiver == donor or receiver.up_headroom_mw <= 0:
                    continue
                receiver_sf = shift_by_bus.get(receiver.bus)
                if receiver_sf is None:
                    continue

                pair_effect = (receiver_sf - donor_sf) / 100.0
                relief_per_mw = -flow_sign * pair_effect
                if relief_per_mw <= 1e-9:
                    continue

                required = (
                    required_abs_relief / relief_per_mw
                    if required_abs_relief > 0
                    else 0.0
                )
                feasible = min(
                    required if required > 0 else 1.0,
                    donor.down_headroom_mw,
                    receiver.up_headroom_mw,
                )
                if feasible <= 0:
                    continue
                predicted_relief = feasible * relief_per_mw

                candidates.append(RedispatchCandidate(
                    donor=donor,
                    receiver=receiver,
                    donor_shift_pct=donor_sf,
                    receiver_shift_pct=receiver_sf,
                    relief_per_mw=relief_per_mw,
                    required_redispatch_mw=required,
                    feasible_redispatch_mw=feasible,
                    predicted_relief_mw=predicted_relief,
                    predicted_to_target=(
                        required_abs_relief <= 1e-9
                        or predicted_relief + 1e-6 >= required_abs_relief
                    ),
                ))

        candidates.sort(
            key=lambda c: (
                not c.predicted_to_target,
                c.required_redispatch_mw if c.predicted_to_target else float("inf"),
                -c.predicted_relief_mw,
            )
        )

        base_info = {
            **base,
            "target_loading_pct": target_loading_pct,
            "estimated_required_abs_mw_relief": required_abs_relief,
            "flow_sign": flow_sign,
        }
        return base_info, ref, candidates[:max_candidates]

    def _secondary_violations(
        self,
        base_branches: list[dict[str, Any]],
        post_branches: list[dict[str, Any]],
        base_buses: list[dict[str, Any]],
        post_buses: list[dict[str, Any]],
        monitored: BranchIdentity,
    ) -> list[dict[str, Any]]:
        issues: list[dict[str, Any]] = []

        def key(r):
            a, b = int(r["from"]), int(r["to"])
            return (min(a, b), max(a, b), str(r["circuit"]))

        base_map = {key(r): r for r in base_branches}
        for post in post_branches:
            if _same_branch(post, monitored):
                continue
            base = base_map.get(key(post))
            if not base:
                continue
            bp = base.get("loading_pct")
            pp = post.get("loading_pct")
            if bp is None or pp is None:
                continue
            if pp >= 100 and bp < 100:
                issues.append({
                    "type": "NEW_THERMAL_VIOLATION",
                    "branch": f"{post['from']}-{post['to']} {post['circuit']}",
                    "base_loading_pct": bp,
                    "post_loading_pct": pp,
                })
            elif bp >= 100 and pp - bp > 5.0:
                issues.append({
                    "type": "WORSENED_EXISTING_THERMAL",
                    "branch": f"{post['from']}-{post['to']} {post['circuit']}",
                    "base_loading_pct": bp,
                    "post_loading_pct": pp,
                })

        base_v = {int(r["bus"]): r for r in base_buses}
        for post in post_buses:
            base = base_v.get(int(post["bus"]))
            if not base:
                continue
            bv = base.get("voltage_pu")
            pv = post.get("voltage_pu")
            if bv is None or pv is None:
                continue
            if pv < 0.95 <= bv:
                issues.append({
                    "type": "NEW_LOW_VOLTAGE",
                    "bus": post["bus"],
                    "base_voltage_pu": bv,
                    "post_voltage_pu": pv,
                })
            elif bv < 0.95 and pv < bv - 0.02:
                issues.append({
                    "type": "WORSENED_LOW_VOLTAGE",
                    "bus": post["bus"],
                    "base_voltage_pu": bv,
                    "post_voltage_pu": pv,
                })
        return issues

    def test_candidate(
        self,
        monitored: BranchIdentity,
        candidate: RedispatchCandidate,
        *,
        target_loading_pct: float,
    ) -> dict[str, Any]:
        base_branches = self.doctor.branch_snapshot()
        base_buses = self.doctor.bus_snapshot()
        base_mon = self._branch(base_branches, monitored)

        delta = candidate.feasible_redispatch_mw
        donor_target = candidate.donor.mw - delta
        receiver_target = candidate.receiver.mw + delta

        self.adapter.save_state()
        try:
            self.generators.set_mw(candidate.donor, donor_target)
            self.generators.set_mw(candidate.receiver, receiver_target)
            self.adapter.run_script("EnterMode(PowerFlow); SolvePowerFlow(RECTNEWT);")

            actual_donor = self.generators.read_mw(
                candidate.donor.bus, candidate.donor.gen_id
            )
            actual_receiver = self.generators.read_mw(
                candidate.receiver.bus, candidate.receiver.gen_id
            )

            post_branches = self.doctor.branch_snapshot()
            post_buses = self.doctor.bus_snapshot()
            post_mon = self._branch(post_branches, monitored)

            drift = False
            if actual_donor is not None and abs(actual_donor - donor_target) > 1.0:
                drift = True
            if actual_receiver is not None and abs(actual_receiver - receiver_target) > 1.0:
                drift = True

            secondary = self._secondary_violations(
                base_branches, post_branches, base_buses, post_buses, monitored
            )
        finally:
            self.adapter.load_state()

        base_pct = float(base_mon["loading_pct"])
        post_pct = float(post_mon["loading_pct"])
        solved_relief_pct_points = base_pct - post_pct

        return {
            "donor": {
                "bus": candidate.donor.bus,
                "id": candidate.donor.gen_id,
                "base_mw": candidate.donor.mw,
                "target_mw": donor_target,
                "actual_mw_after_solve": actual_donor,
                "down_headroom_mw": candidate.donor.down_headroom_mw,
                "shift_factor_pct": candidate.donor_shift_pct,
            },
            "receiver": {
                "bus": candidate.receiver.bus,
                "id": candidate.receiver.gen_id,
                "base_mw": candidate.receiver.mw,
                "target_mw": receiver_target,
                "actual_mw_after_solve": actual_receiver,
                "up_headroom_mw": candidate.receiver.up_headroom_mw,
                "shift_factor_pct": candidate.receiver_shift_pct,
            },
            "redispatch_mw_each_direction": delta,
            "predicted_relief_mw": candidate.predicted_relief_mw,
            "base_loading_pct": base_pct,
            "post_loading_pct": post_pct,
            "solved_relief_pct_points": solved_relief_pct_points,
            "target_loading_pct": target_loading_pct,
            "target_met": post_pct <= target_loading_pct + 1e-6,
            "control_drift": drift,
            "secondary_violations": secondary,
            "security_pass": not secondary and not drift,
            "state_restored": True,
        }

    def run(
        self,
        monitored: BranchIdentity,
        *,
        reference_bus: int | None = None,
        target_loading_pct: float = 98.0,
        max_tested: int = 8,
    ) -> RemedySearchResult:
        base, ref, candidates = self.screen(
            monitored,
            reference_bus=reference_bus,
            target_loading_pct=target_loading_pct,
        )

        screening = [{
            "donor_bus": c.donor.bus,
            "donor_id": c.donor.gen_id,
            "receiver_bus": c.receiver.bus,
            "receiver_id": c.receiver.gen_id,
            "donor_shift_pct": c.donor_shift_pct,
            "receiver_shift_pct": c.receiver_shift_pct,
            "relief_per_mw": c.relief_per_mw,
            "required_redispatch_mw": c.required_redispatch_mw,
            "feasible_redispatch_mw": c.feasible_redispatch_mw,
            "predicted_relief_mw": c.predicted_relief_mw,
            "predicted_to_target": c.predicted_to_target,
        } for c in candidates]

        tested = [
            self.test_candidate(
                monitored, candidate, target_loading_pct=target_loading_pct
            )
            for candidate in candidates[:max_tested]
        ]

        # Production ranking: solved security first, target achievement second,
        # smallest actual intervention third, strongest solved relief fourth.
        tested.sort(key=lambda r: (
            not r["security_pass"],
            not r["target_met"],
            r["redispatch_mw_each_direction"],
            -r["solved_relief_pct_points"],
        ))

        recommended = next(
            (r for r in tested if r["security_pass"] and r["target_met"]),
            None,
        )
        if recommended is None:
            recommended = next((r for r in tested if r["security_pass"]), None)

        findings: list[Finding] = []
        if recommended:
            findings.append(Finding(
                finding_id="REMEDY-1",
                severity="INFO",
                category="REMEDY",
                title=(
                    f"Redispatch {recommended['redispatch_mw_each_direction']:.1f} MW: "
                    f"Gen {recommended['donor']['bus']}/{recommended['donor']['id']} ↓, "
                    f"Gen {recommended['receiver']['bus']}/{recommended['receiver']['id']} ↑"
                ),
                summary=(
                    f"Solved monitored loading changes from "
                    f"{recommended['base_loading_pct']:.1f}% to "
                    f"{recommended['post_loading_pct']:.1f}%. "
                    f"Security pass: {recommended['security_pass']}."
                ),
                simple_explanation=(
                    "The tool moves the same MW away from a location that pushes power onto the constraint "
                    "and toward a location that has a more relieving electrical effect, then solves the case "
                    "to make sure the remedy actually works."
                ),
                evidence=[recommended],
                confidence="HIGH" if self.adapter.solver_backed else "DEMO",
            ))

        return RemedySearchResult(
            monitored={
                "from": monitored.from_bus,
                "to": monitored.to_bus,
                "circuit": monitored.circuit,
            },
            reference_bus=ref,
            target_loading_pct=target_loading_pct,
            base=base,
            screening=screening,
            tested=tested,
            recommended=recommended,
            findings=findings,
        )
