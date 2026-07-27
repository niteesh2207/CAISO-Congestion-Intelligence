from __future__ import annotations
from dataclasses import dataclass
from typing import Any
import math

from ..models import Finding
from .causal_graph import CausalEvidenceGraph
from .contingency import BranchOutageStudy
from .model_doctor import ModelDoctor
from .object_resolver import BranchIdentity, BranchResolver
from .sensitivity import SensitivityEngine


def same_branch(row: dict[str, Any], branch: BranchIdentity) -> bool:
    return (
        {int(row["from"]), int(row["to"])} == {branch.from_bus, branch.to_bus}
        and str(row["circuit"]).strip() == str(branch.circuit).strip()
    )


def finite(value: Any) -> bool:
    return isinstance(value, (int, float)) and math.isfinite(float(value))


def explanation_coverage(actual_delta: float | None, predicted_delta: float | None) -> float | None:
    """
    1.0 = linear LODF prediction exactly matches the solved MW change.
    0.0 = poor explanatory match.
    """
    if not finite(actual_delta) or not finite(predicted_delta):
        return None
    actual = float(actual_delta)
    predicted = float(predicted_delta)
    scale = max(abs(actual), abs(predicted), 1.0)
    score = 1.0 - abs(actual - predicted) / scale
    return max(0.0, min(1.0, score))


def confidence_from_coverage(
    coverage: float | None,
    *,
    solver_backed: bool,
    topology_warning: bool,
) -> str:
    if topology_warning:
        return "LOW"
    if coverage is None:
        return "LOW"
    # Demo mode can demonstrate logic but can never receive real-solver confidence.
    if not solver_backed:
        return "DEMO"
    if coverage >= 0.85:
        return "HIGH"
    if coverage >= 0.60:
        return "MEDIUM"
    return "LOW"


@dataclass
class CausalDiagnosisResult:
    monitored: dict[str, Any]
    outage: dict[str, Any]
    base: dict[str, Any]
    solved_post_event: dict[str, Any]
    linear_explanation: dict[str, Any]
    sensitivity_exposure: dict[str, Any]
    causal_graph: dict[str, Any]
    confidence: str
    findings: list[Finding]
    network_replay: dict[str, Any]


class CausalDiagnosis:
    def __init__(self, adapter) -> None:
        self.adapter = adapter
        self.doctor = ModelDoctor(adapter)
        self.sensitivity = SensitivityEngine(adapter)
        self.resolver = BranchResolver(adapter)

    def _branch(self, rows: list[dict[str, Any]], identity: BranchIdentity) -> dict[str, Any]:
        matches = [row for row in rows if same_branch(row, identity)]
        if len(matches) != 1:
            raise RuntimeError(
                f"Expected exactly one branch for {identity.from_bus}-{identity.to_bus} "
                f"circuit {identity.circuit}; found {len(matches)}."
            )
        return matches[0]

    def run(
        self,
        *,
        monitored: BranchIdentity,
        outage: BranchIdentity,
        reference_bus: int | None = None,
    ) -> CausalDiagnosisResult:
        # Resolve both against the loaded model first.
        mon_resolved = self.resolver.resolve(monitored)
        out_resolved = self.resolver.resolve(outage)

        # Actual AC/Simulator scenario comparison (demo adapter provides its own
        # deterministic synthetic solve response).
        scenario = BranchOutageStudy(self.adapter).run(outage)

        base_mon = self._branch(scenario.base_branches, monitored)
        post_mon = self._branch(scenario.post_branches, monitored)
        base_out = self._branch(scenario.base_branches, outage)

        actual_delta_mw = None
        if finite(base_mon.get("mw")) and finite(post_mon.get("mw")):
            actual_delta_mw = float(post_mon["mw"]) - float(base_mon["mw"])

        lodf_rows = self.sensitivity.lodf(outage)
        lodf_mon = next(
            (float(row["lodf_pct"]) for row in lodf_rows if same_branch(row, monitored)),
            None,
        )

        predicted_delta_mw = None
        if lodf_mon is not None and finite(base_out.get("mw")):
            predicted_delta_mw = (lodf_mon / 100.0) * float(base_out["mw"])

        predicted_post_mw = (
            float(base_mon["mw"]) + predicted_delta_mw
            if finite(base_mon.get("mw")) and finite(predicted_delta_mw)
            else None
        )

        topology_warning = False
        if lodf_mon is not None and abs(lodf_mon) > 1000:
            # PowerWorld may return very large factors when the outage produces
            # a topology/islanding condition. Do not treat this as normal flow sharing.
            topology_warning = True

        coverage = explanation_coverage(actual_delta_mw, predicted_delta_mw)
        confidence = confidence_from_coverage(
            coverage,
            solver_backed=self.adapter.solver_backed,
            topology_warning=topology_warning,
        )

        # Sensitivity reference is deliberately explicit. If user didn't provide
        # one, use the monitored branch's resolved "to" terminal as a reference,
        # but never call it the physical sink.
        ref_bus = reference_bus if reference_bus is not None else int(mon_resolved["to"])
        screen = self.sensitivity.bus_shift_screen(monitored, sink_bus=ref_bus, top_n=8)

        # Generator and load presence at the most sensitive buses: useful
        # screening context, NOT an additive contribution decomposition.
        gen_bus_field = self.doctor.catalog.choose("GEN", ["BusNum"])
        gen_mw_field = self.doctor.catalog.choose("GEN", ["GenMW"])
        generators = []
        if gen_bus_field and gen_mw_field:
            for row in self.adapter.get_rows("GEN", [gen_bus_field, gen_mw_field]):
                try:
                    generators.append({
                        "bus": int(row[gen_bus_field]),
                        "mw": float(row[gen_mw_field]),
                    })
                except (TypeError, ValueError):
                    pass

        load_bus_field = self.doctor.catalog.choose("LOAD", ["BusNum"])
        load_mw_field = self.doctor.catalog.choose("LOAD", ["LoadMW"])
        loads = []
        if load_bus_field and load_mw_field:
            for row in self.adapter.get_rows("LOAD", [load_bus_field, load_mw_field]):
                try:
                    loads.append({
                        "bus": int(row[load_bus_field]),
                        "mw": float(row[load_mw_field]),
                    })
                except (TypeError, ValueError):
                    pass

        gen_by_bus: dict[int, float] = {}
        for g in generators:
            gen_by_bus[g["bus"]] = gen_by_bus.get(g["bus"], 0.0) + g["mw"]
        load_by_bus: dict[int, float] = {}
        for ld in loads:
            load_by_bus[ld["bus"]] = load_by_bus.get(ld["bus"], 0.0) + ld["mw"]

        def enrich(rows):
            return [
                {
                    **row,
                    "generation_mw_at_bus": gen_by_bus.get(row["source_bus"], 0.0),
                    "load_mw_at_bus": load_by_bus.get(row["source_bus"], 0.0),
                    "interpretation": (
                        "WORSENING_SENSITIVITY" if row["shift_factor_pct"] > 0
                        else "RELIEVING_SENSITIVITY" if row["shift_factor_pct"] < 0
                        else "NEUTRAL"
                    ),
                }
                for row in rows
            ]

        exposure = {
            "reference_bus": ref_bus,
            "reference_rule": (
                "USER_SELECTED" if reference_bus is not None
                else "MONITORED_BRANCH_TO_TERMINAL_AS_REFERENCE"
            ),
            "worsen": enrich(screen["worsen"]),
            "relieve": enrich(screen["relieve"]),
            "warning": (
                "These are signed injection sensitivities relative to the reference bus. "
                "They are not a literal additive decomposition of the current branch flow."
            ),
        }

        graph = CausalEvidenceGraph()
        graph.add_node(
            "base_stress", "STATE", "Base monitored loading",
            base_mon.get("loading_pct"), "FACT",
            "HIGH" if self.adapter.solver_backed else "DEMO"
        )
        graph.add_node(
            "outage_flow", "STATE", "Pre-outage flow on outaged branch",
            base_out.get("mw"), "FACT",
            "HIGH" if self.adapter.solver_backed else "DEMO"
        )
        graph.add_node(
            "lodf", "SENSITIVITY", "LODF monitored←outage",
            lodf_mon, "FACT",
            "HIGH" if self.adapter.solver_backed and not topology_warning else confidence
        )
        graph.add_node(
            "predicted_delta", "DERIVED", "Linear predicted monitored MW change",
            predicted_delta_mw, "DERIVED", confidence
        )
        graph.add_node(
            "actual_delta", "STATE", "Solved monitored MW change",
            actual_delta_mw, "FACT",
            "HIGH" if self.adapter.solver_backed else "DEMO"
        )
        graph.add_node(
            "post_loading", "STATE", "Solved post-event loading",
            post_mon.get("loading_pct"), "FACT",
            "HIGH" if self.adapter.solver_backed else "DEMO"
        )
        graph.add_node(
            "coverage", "VALIDATION", "Linear explanation coverage",
            coverage, "DERIVED", confidence
        )
        graph.add_node(
            "sensitivity_exposure", "INTERPRETATION", "Injection sensitivity exposure",
            {
                "reference_bus": ref_bus,
                "top_worsen": exposure["worsen"][:3],
                "top_relieve": exposure["relieve"][:3],
            },
            "INTERPRETATION", confidence
        )

        graph.link("outage_flow", "predicted_delta", "scaled_by_LODF")
        graph.link("lodf", "predicted_delta", "distribution_factor")
        graph.link("predicted_delta", "coverage", "compared_with_actual")
        graph.link("actual_delta", "coverage", "compared_with_linear_prediction")
        graph.link("actual_delta", "post_loading", "contributes_to_post_event_state")
        graph.link("base_stress", "post_loading", "starting_condition")
        graph.link("sensitivity_exposure", "post_loading", "identifies_candidate_injection_directions")

        findings: list[Finding] = []
        if finite(post_mon.get("loading_pct")) and float(post_mon["loading_pct"]) >= 100:
            findings.append(Finding(
                finding_id="CAUSE-OVERLOAD",
                severity="CRITICAL",
                category="CAUSAL_DIAGNOSIS",
                title=(
                    f"Branch {post_mon['from']}–{post_mon['to']} {post_mon['circuit']} "
                    f"reaches {post_mon['loading_pct']:.1f}%"
                ),
                summary=(
                    f"Base loading was {base_mon['loading_pct']:.1f}% and the solved outage changed "
                    f"MW flow by {actual_delta_mw:+.1f} MW."
                ),
                simple_explanation=(
                    "The line was already carrying significant power, and the outage changed how the remaining "
                    "network had to carry that power."
                ),
                evidence=[{
                    "base_loading_pct": base_mon.get("loading_pct"),
                    "post_loading_pct": post_mon.get("loading_pct"),
                    "actual_delta_mw": actual_delta_mw,
                    "lodf_pct": lodf_mon,
                    "predicted_delta_mw": predicted_delta_mw,
                }],
                confidence=confidence,
            ))

        if topology_warning:
            findings.append(Finding(
                finding_id="CAUSE-TOPOLOGY",
                severity="HIGH",
                category="VALIDATION",
                title="Sensitivity indicates a topology/islanding condition",
                summary="The LODF magnitude is too large to treat as an ordinary redistribution factor.",
                simple_explanation="This outage appears to change network connectivity enough that a normal linear flow-sharing explanation is unreliable.",
                evidence=[{"lodf_pct": lodf_mon}],
                confidence="HIGH" if self.adapter.solver_backed else "DEMO",
            ))

        base = {
            "monitored_flow_mw": base_mon.get("mw"),
            "monitored_mva": base_mon.get("mva"),
            "monitored_loading_pct": base_mon.get("loading_pct"),
            "outage_line_flow_mw": base_out.get("mw"),
        }
        solved_post = {
            "monitored_flow_mw": post_mon.get("mw"),
            "monitored_mva": post_mon.get("mva"),
            "monitored_loading_pct": post_mon.get("loading_pct"),
            "actual_delta_mw": actual_delta_mw,
        }
        linear = {
            "lodf_pct": lodf_mon,
            "predicted_delta_mw": predicted_delta_mw,
            "predicted_post_mw": predicted_post_mw,
            "actual_delta_mw": actual_delta_mw,
            "explanation_coverage": coverage,
            "topology_warning": topology_warning,
        }

        # Network replay data: full base/post branch and bus states are returned
        # to the front end. No geometry is fabricated; the demo UI reuses its
        # existing network coordinates. Real oneline/geography extraction is later.
        replay = {
            "base_branches": scenario.base_branches,
            "post_branches": scenario.post_branches,
            "base_buses": scenario.base_buses,
            "post_buses": scenario.post_buses,
        }

        return CausalDiagnosisResult(
            monitored={
                "from": monitored.from_bus, "to": monitored.to_bus, "circuit": monitored.circuit
            },
            outage={
                "from": outage.from_bus, "to": outage.to_bus, "circuit": outage.circuit
            },
            base=base,
            solved_post_event=solved_post,
            linear_explanation=linear,
            sensitivity_exposure=exposure,
            causal_graph=graph.to_dict(),
            confidence=confidence,
            findings=findings,
            network_replay=replay,
        )
