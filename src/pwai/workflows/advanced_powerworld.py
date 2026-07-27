from __future__ import annotations

from typing import Any
from pathlib import Path

from ..field_catalog import FieldCatalog
from .capabilities import CapabilityRegistry


class AdvancedPowerWorldGateway:
    """
    License-aware gateway for advanced Simulator tools.

    Real execution is intentionally thin: the configured PowerWorld tool owns
    the physics and options. The AI layer validates capability, executes the
    documented script command, then discovers result schemas dynamically.
    """

    def __init__(self, adapter) -> None:
        self.adapter = adapter
        self.capabilities = CapabilityRegistry(adapter)
        self.catalog = FieldCatalog(adapter)

    def status(self) -> dict[str, Any]:
        cap = self.capabilities.snapshot()
        return {
            "ATC": cap["capabilities"].get("ATC"),
            "PVQV": cap["capabilities"].get("PVQV"),
            "TRANSIENT_STABILITY": cap["capabilities"].get("TRANSIENT_STABILITY"),
            "commands": {
                "ATC": "ATCDetermine([seller],[buyer],DoDistributed,DoMultipleScenarios)",
                "PV": "PVRun([elementSource],[elementSink])",
                "QV": 'QVRun("filename",InErrorMakeBaseSolvable,DoDistributed)',
                "TS_VALIDATE": "TSValidate",
                "TS_SOLVE": 'TSSolve("ContingencyName",[StartTime,StopTime,StepSize,StepInCycles])',
                "TS_SOLVE_ALL": "TSSolveAll(DoDistributed)",
            },
            "real_machine_acceptance_validated": False,
        }

    def _require(self, name: str) -> None:
        cap = self.capabilities.snapshot()["capabilities"].get(name, {})
        if not cap.get("available"):
            raise RuntimeError(
                f"{name} is not available according to ProgramInformation."
            )

    def atc_bus_to_bus(
        self,
        source_bus: int,
        sink_bus: int,
        *,
        distributed: bool = False,
        multiple_scenarios: bool = False,
    ) -> dict[str, Any]:
        self._require("ATC")
        command = (
            f"ATCDetermine([BUS {int(source_bus)}], [BUS {int(sink_bus)}], "
            f"{'YES' if distributed else 'NO'}, "
            f"{'YES' if multiple_scenarios else 'NO'});"
        )
        if not self.adapter.solver_backed:
            return {
                "mode": "DEMO_GATEWAY_ONLY",
                "command": command,
                "results": [],
                "warning": "No synthetic ATC MW result is invented.",
            }
        status = self.adapter.run_script(command)

        results = []
        try:
            fields = self.catalog.fields("TRANSFERLIMITER")
            variables = [f.variable for f in fields]
            if variables:
                # Keep result discovery conservative; retrieve only fields with
                # useful names and let field catalog define the actual schema.
                selected = [
                    f.variable for f in fields
                    if any(
                        term in f"{f.variable} {f.description}".lower()
                        for term in ["limit", "conting", "element", "direction", "transfer"]
                    )
                ][:20]
                if selected:
                    results = self.adapter.get_rows(
                        "TRANSFERLIMITER", selected
                    )
        except Exception:
            pass
        return {
            "mode": "POWERWORLD",
            "command": command,
            "status": status,
            "results": results,
        }

    def pv_run(self, source_group: str, sink_group: str) -> dict[str, Any]:
        self._require("PVQV")
        command = (
            f'PVRun([INJECTIONGROUP "{source_group}"], '
            f'[INJECTIONGROUP "{sink_group}"]);'
        )
        if not self.adapter.solver_backed:
            return {
                "mode": "DEMO_GATEWAY_ONLY",
                "command": command,
                "warning": "No synthetic PV curve is invented.",
            }
        return {
            "mode": "POWERWORLD",
            "command": command,
            "status": self.adapter.run_script(command),
        }

    def qv_run(
        self,
        filename: str,
        *,
        make_base_solvable: bool = False,
        distributed: bool = False,
    ) -> dict[str, Any]:
        self._require("PVQV")
        command = (
            f'QVRun("{filename}",'
            f'{"YES" if make_base_solvable else "NO"},'
            f'{"YES" if distributed else "NO"});'
        )
        if not self.adapter.solver_backed:
            return {
                "mode": "DEMO_GATEWAY_ONLY",
                "command": command,
                "warning": "No synthetic QV margin is invented.",
            }
        return {
            "mode": "POWERWORLD",
            "command": command,
            "status": self.adapter.run_script(command),
        }

    def transient_validate(self) -> dict[str, Any]:
        self._require("TRANSIENT_STABILITY")
        if not self.adapter.solver_backed:
            return {
                "mode": "DEMO_GATEWAY_ONLY",
                "command": "TSValidate;",
                "validation": [],
                "warning": "No synthetic transient validation is invented.",
            }
        status = self.adapter.run_script("TSValidate;")
        rows = []
        try:
            fields = self.catalog.fields("TSVALIDATION")
            selected = [f.variable for f in fields][:20]
            if selected:
                rows = self.adapter.get_rows("TSVALIDATION", selected)
        except Exception:
            pass
        return {
            "mode": "POWERWORLD",
            "command": "TSValidate;",
            "status": status,
            "validation": rows,
        }

    def transient_solve(
        self,
        contingency_name: str,
        *,
        start_time: float = 0.0,
        stop_time: float = 10.0,
        step_size: float = 0.01,
        step_in_cycles: bool = False,
    ) -> dict[str, Any]:
        self._require("TRANSIENT_STABILITY")
        command = (
            f'TSSolve("{contingency_name}",'
            f'[{float(start_time)},{float(stop_time)},{float(step_size)},'
            f'{"YES" if step_in_cycles else "NO"}]);'
        )
        if not self.adapter.solver_backed:
            return {
                "mode": "DEMO_GATEWAY_ONLY",
                "command": command,
                "warning": "No synthetic dynamic trajectory is invented.",
            }
        return {
            "mode": "POWERWORLD",
            "command": command,
            "status": self.adapter.run_script(command),
        }


class IBRModelInspector:
    """
    Schema-based inventory of dynamic/IBR model evidence.

    This does not infer stability. It only reports whether the case exposes
    model fields associated with grid-following/grid-forming/BESS resources.
    """

    def __init__(self, adapter) -> None:
        self.adapter = adapter
        self.catalog = FieldCatalog(adapter)

    def inspect(self) -> dict[str, Any]:
        matches = []
        try:
            fields = self.catalog.fields("GEN")
        except Exception:
            fields = []

        terms = [
            "regfm", "grid-form", "reec", "regc", "repca", "repc",
            "machine model", "exciter", "governor", "plant controller",
            "unit type",
        ]
        for f in fields:
            hay = f"{f.variable} {f.description}".lower()
            if any(term in hay for term in terms):
                matches.append({
                    "variable": f.variable,
                    "description": f.description,
                    "type": f.data_type,
                })

        return {
            "matching_generator_fields": matches[:100],
            "supports_bess_model_family_in_powerworld_docs": True,
            "supports_grid_forming_models_in_simulator24": True,
            "stability_conclusion": "NOT_COMPUTED",
            "guardrail": (
                "Model presence is not stability performance. Transient analysis "
                "must be validated and solved before making a dynamic conclusion."
            ),
        }
