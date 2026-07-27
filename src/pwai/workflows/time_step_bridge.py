from __future__ import annotations

from typing import Any


class TimeStepBridge:
    """
    Conservative automation bridge to an already-configured PowerWorld Time Step
    Simulation (TSS).

    Official Simulator 24 AUX commands:
      TimeStepDoSinglePoint(ISO8601DateTime);
      TimeStepDoRun(ISO8601StartDateTime,ISO8601EndDateTime);

    V0.12 does not silently create/delete PowerWorld timepoints or overwrite TSS
    input grids. It can execute an existing TSS configuration and separately
    provides a product-owned multi-hour replay engine.
    """

    def __init__(self, adapter) -> None:
        self.adapter = adapter

    def capabilities(self) -> dict[str, Any]:
        return {
            "available_in_base_simulator": True,
            "single_point_command": "TimeStepDoSinglePoint(ISO8601DateTime)",
            "range_command": "TimeStepDoRun(ISO8601StartDateTime,ISO8601EndDateTime)",
            "supported_solution_types_documented": [
                "Single Solution",
                "Unconstrained OPF",
                "OPF",
                "SCOPF",
            ],
            "real_machine_acceptance_validated": False,
            "guardrail": (
                "V0.12 only executes a preconfigured native TSS. It does not "
                "invent or overwrite PowerWorld timepoint/input-grid structure."
            ),
        }

    def run_single_point(self, iso8601_datetime: str) -> str:
        if not self.adapter.solver_backed:
            return f"DEMO_TSS_SINGLE_POINT:{iso8601_datetime}"
        return self.adapter.run_script(
            f"TimeStepDoSinglePoint({iso8601_datetime});"
        )

    def run_range(self, start_iso: str, end_iso: str) -> str:
        if not self.adapter.solver_backed:
            return f"DEMO_TSS_RANGE:{start_iso}->{end_iso}"
        return self.adapter.run_script(
            f"TimeStepDoRun({start_iso},{end_iso});"
        )
