from __future__ import annotations

from typing import Any

from ..field_catalog import FieldCatalog
from ..resource_utils import project_or_package_resource
from .generator_controls import GeneratorInventory
from .model_doctor import ModelDoctor
from .object_resolver import BranchIdentity
from .bess import BESSIntelligence
import json


class RASIntelligence:
    """
    Remedial Action Scheme / SPS / operating-guide intelligence.

    Real mode:
      - discovers PowerWorld RemedialAction / RemedialActionElement schemas;
      - reports arming/action evidence;
      - does not invent missing RAS logic.

    Demo mode:
      - uses explicit synthetic RAS configuration and can compare the selected
        outage with and without the configured RAS action.
    """

    RESULT_OBJECT_CANDIDATES = [
        "REMEDIALACTION",
        "REMEDIALACTIONELEMENT",
        "REMEDIALACTIONELEM",
    ]

    def __init__(self, adapter) -> None:
        self.adapter = adapter
        self.catalog = FieldCatalog(adapter)
        self.doctor = ModelDoctor(adapter)
        self.gens = GeneratorInventory(adapter)

    def _demo_config(self) -> dict[str, Any]:
        p = project_or_package_resource("config", "ras_schemes.json")
        return json.loads(p.read_text(encoding="utf-8"))

    def inventory(self) -> dict[str, Any]:
        if not self.adapter.solver_backed:
            cfg = self._demo_config()
            return {
                "mode": "DEMO",
                "provenance": cfg["provenance"],
                "schemes": cfg["schemes"],
                "count": len(cfg["schemes"]),
            }

        discovered = {}
        for obj in self.RESULT_OBJECT_CANDIDATES:
            try:
                fields = self.catalog.fields(obj)
            except Exception:
                fields = []
            if fields:
                selected = [
                    f.variable for f in fields
                    if any(
                        term in f"{f.variable} {f.description}".lower()
                        for term in [
                            "name", "label", "armed", "arming", "status",
                            "criteria", "action", "element", "skip", "delay",
                        ]
                    )
                ][:30]
                rows = []
                if selected:
                    try:
                        rows = self.adapter.get_rows(obj, selected)
                    except Exception:
                        rows = []
                discovered[obj] = {
                    "fields": [
                        {"variable": f.variable, "description": f.description}
                        for f in fields[:100]
                    ],
                    "rows": rows[:200],
                }

        return {
            "mode": "POWERWORLD_SCHEMA_DISCOVERY",
            "objects": discovered,
            "count": sum(len(v["rows"]) for v in discovered.values()),
            "warning": (
                "Only fields actually exposed by the running Simulator are reported. "
                "No RAS action or arming logic is inferred when its schema is absent."
            ),
        }

    def _set_branch_status(self, identity: BranchIdentity, status: str) -> None:
        f = self.doctor.branch_fields()
        if not f["status"]:
            raise RuntimeError("Branch status field could not be resolved.")
        self.adapter.change_single(
            "BRANCH",
            [f["from"], f["to"], f["circuit"], f["status"]],
            [identity.from_bus, identity.to_bus, identity.circuit, status],
        )

    def _branch(self, identity: BranchIdentity) -> dict[str, Any]:
        rows = [
            r for r in self.doctor.branch_snapshot()
            if {int(r["from"]), int(r["to"])}
            == {identity.from_bus, identity.to_bus}
            and str(r["circuit"]) == str(identity.circuit)
        ]
        return rows[0] if len(rows) == 1 else {}

    def demo_effectiveness(self, scheme_name: str | None = None) -> dict[str, Any]:
        if self.adapter.solver_backed:
            return {
                "mode": "POWERWORLD_NATIVE_RAS_RECOMMENDED",
                "inventory": self.inventory(),
                "warning": (
                    "V1.1 does not flatten arbitrary real RAS logic into a synthetic replay. "
                    "Use native Contingency Analysis with Remedial Actions enabled and compare results."
                ),
            }

        cfg = self._demo_config()
        schemes = cfg["schemes"]
        scheme = next(
            (
                s for s in schemes
                if scheme_name is None or s["name"].lower() == scheme_name.lower()
            ),
            schemes[0] if schemes else None,
        )
        if scheme is None:
            raise RuntimeError("No demo RAS scheme is configured.")

        trig = scheme["trigger"]
        outage = BranchIdentity(
            int(trig["from"]), int(trig["to"]), str(trig.get("circuit", "1"))
        )
        monitored = BranchIdentity(301, 501, "1")

        self.adapter.save_state()
        try:
            self._set_branch_status(outage, "Open")
            self.adapter.run_script("EnterMode(PowerFlow); SolvePowerFlow(RECTNEWT);")
            without_ras = self._branch(monitored)
        finally:
            self.adapter.load_state()

        # The demo outage solver is intentionally simple and does not solve a
        # combined outage + redispatch AC case. Use the product's deterministic
        # OTDF BESS sensitivity to derive the synthetic RAS action effect.
        bess_action = next(
            a for a in scheme["actions"] if a["type"] == "BESS_DISCHARGE"
        )
        balance_action = next(
            a for a in scheme["actions"] if a["type"] == "GEN_BALANCE"
        )
        screen = BESSIntelligence(self.adapter).screen(
            battery_mw=float(bess_action["mw"]),
            monitored=monitored,
            outage=outage,
            reference_bus=int(balance_action["bus"]),
            top_n=20,
        )
        bus_row = next(
            r for r in screen["discharge_best_relief"] + screen["discharge_worst"]
            if int(r["bus"]) == int(bess_action["bus"])
        )
        delta_mw = float(bus_row["discharge_effect_mw"])

        with_ras = dict(without_ras)
        if with_ras.get("mw") is not None:
            with_ras["mw"] = float(with_ras["mw"]) + delta_mw
            with_ras["mva"] = abs(float(with_ras["mw"])) * 1.045
            if with_ras.get("limit_mva") not in (None, 0):
                with_ras["loading_pct"] = (
                    100.0 * float(with_ras["mva"])
                    / float(with_ras["limit_mva"])
                )
        with_ras["evidence_class"] = "DEMO_OTDF_DERIVED"

        relief = None
        if without_ras.get("loading_pct") is not None and with_ras.get("loading_pct") is not None:
            relief = (
                float(without_ras["loading_pct"])
                - float(with_ras["loading_pct"])
            )

        return {
            "mode": "DEMO_EFFECTIVENESS",
            "scheme": scheme,
            "outage": vars(outage),
            "monitored": vars(monitored),
            "without_ras": without_ras,
            "with_ras": with_ras,
            "loading_relief_pct_points": relief,
            "state_restored": True,
            "guardrail": (
                "Demo RAS actions are explicit synthetic development inputs. "
                "Real RAS effectiveness must come from PowerWorld native contingency/RAS processing."
            ),
        }
