from __future__ import annotations

from typing import Any

from .build_guardian import BuildGuardian
from .capabilities import CapabilityRegistry
from .constraint_economics import ConstraintEconomics
from .model_doctor import ModelDoctor
from .native_contingency import NativeContingencyEngine
from .optimization import OptimizationIntelligence
from .study_memory import StudyMemory


class AutonomousGridInvestigator:
    """
    Evidence-first autonomous study sequence.

    It does not invent a problem statement. It:
    1. checks solver provenance and capabilities;
    2. runs model-health screens;
    3. runs N-1;
    4. optionally runs OPF economics if licensed;
    5. ranks what deserves human attention next;
    6. writes evidence-linked study memory / knowledge-graph edges.
    """

    def __init__(self, adapter, case_name: str) -> None:
        self.adapter = adapter
        self.case_name = case_name

    def run(self, *, study_id: str, question: str) -> dict[str, Any]:
        build = BuildGuardian(self.adapter).inspect()
        caps = CapabilityRegistry(self.adapter).snapshot()
        doctor = ModelDoctor(self.adapter)
        findings = [f.model_dump(mode="json") for f in doctor.run(top_n=20)]
        n1 = NativeContingencyEngine(self.adapter).run_all()

        economic = None
        if caps.get("capabilities", {}).get("OPF", {}).get("available"):
            self.adapter.save_state()
            try:
                opt = OptimizationIntelligence(self.adapter).run("OPF")
                econ = ConstraintEconomics(self.adapter)
                snap = econ.snapshot()
                economic = {
                    "optimization": opt.to_dict(),
                    "economics": snap.to_dict(),
                }
            except Exception as exc:
                economic = {"error": str(exc)}
            finally:
                self.adapter.load_state()

        priorities = []
        for f in findings:
            priorities.append({
                "priority": (
                    100 if f["severity"]=="CRITICAL"
                    else 80 if f["severity"]=="HIGH"
                    else 50
                ),
                "type": f["category"],
                "title": f["title"],
                "why": f["summary"],
            })

        for violation in n1.violations:
            priorities.append({
                "priority": 95,
                "type": "N1_VIOLATION",
                "title": (
                    f"{violation.contingency}: "
                    f"{violation.object_id}"
                ),
                "why": (
                    f"Contingency result {violation.category}; "
                    f"{violation.percent if violation.percent is not None else 'unknown'}%."
                ),
            })

        if economic and "error" not in economic:
            for row in economic["economics"].get("binding_branches", []):
                priorities.append({
                    "priority": 90,
                    "type": "ECONOMIC_CONSTRAINT",
                    "title": f"{row['from']}-{row['to']} {row['circuit']}",
                    "why": (
                        f"OPF constraint {row['constraint_status']} with "
                        f"marginal cost {row.get('marginal_cost_per_mva_hour')}."
                    ),
                })

        priorities.sort(key=lambda x: x["priority"], reverse=True)

        recommendations = []
        if n1.violations:
            recommendations += [
                "Run contingency injection-sensitivity relief ranking for the highest-severity N-1 violations.",
                "Test existing BA batteries and balanced redispatch against the leading contingency violations.",
            ]
        if any(f["category"]=="THERMAL" for f in findings):
            recommendations.append(
                "Compare operating remedies against a protected branch-rating upgrade study."
            )
        if economic and "error" not in economic:
            recommendations.append(
                "Use binding-constraint marginal cost and bus LMP decomposition to connect physics to economics."
            )
        recommendations.append(
            "Run scenario ensemble / Grid Time Machine before making an investment conclusion."
        )

        edges = []
        for v in n1.violations:
            edges.append({
                "source": v.contingency,
                "relation": "VIOLATES",
                "target": v.object_id,
                "evidence": {
                    "category": v.category,
                    "percent": v.percent,
                    "source": n1.result_source,
                },
            })
        if economic and "error" not in economic:
            for row in economic["economics"].get("binding_branches", []):
                target = f"{row['from']}-{row['to']} {row['circuit']}"
                edges.append({
                    "source": "OPF",
                    "relation": "BINDS_ON",
                    "target": target,
                    "evidence": {
                        "marginal_cost_per_mva_hour": row.get(
                            "marginal_cost_per_mva_hour"
                        )
                    },
                })

        payload = {
            "build_guardian": build,
            "capabilities": caps,
            "model_findings": findings,
            "n1": n1.to_dict(),
            "economics": economic,
            "priorities": priorities[:30],
            "recommendations": recommendations,
        }

        memory = StudyMemory()
        receipt = memory.remember(
            study_id=study_id,
            question=question,
            study_type="AUTONOMOUS_GRID_INVESTIGATION",
            case_name=self.case_name,
            payload=payload,
            edges=edges,
        )

        return {
            **payload,
            "memory_receipt": receipt,
            "knowledge_graph_edges_added": len(edges),
            "state_restored": True,
            "guardrails": [
                "NO_AUTONOMOUS_BASE_CASE_MUTATION",
                "NO_AI_GENERATED_POWER_FLOW_VALUES",
                "NO_COMPLIANCE_CERTIFICATION",
                "HUMAN_REVIEW_REQUIRED_FOR_ACTION",
            ],
        }
