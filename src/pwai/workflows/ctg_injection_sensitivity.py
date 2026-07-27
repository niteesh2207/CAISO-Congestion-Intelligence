from __future__ import annotations

from dataclasses import dataclass
from typing import Any
import re

from ..field_catalog import FieldCatalog
from .native_contingency import NativeContingencyEngine
from .object_resolver import BranchIdentity
from .sensitivity import SensitivityEngine


@dataclass(frozen=True)
class InjectionSensitivityResult:
    injector: str
    injector_type: str
    injector_bus: int | None
    contingency: str
    element: str
    mw_inj_sensitivity: float | None
    mw_range_inc: float | None
    mw_range_dec: float | None
    mw_effect_inc: float | None
    mw_effect_dec: float | None
    source: str

    def to_dict(self) -> dict[str, Any]:
        return vars(self)


class ContingencyInjectionSensitivityEngine:
    """
    Read/rank violation-level contingency injection sensitivities.

    PowerWorld's public documentation defines the result columns but does not
    publish one guaranteed SimAuto flat-object name for the dedicated display.
    Real mode therefore uses runtime schema discovery and refuses to invent
    results if the object is not exposed.

    Official fallback evidence also exists on LimitViol under
    Sensitivities\\Injection Sensitivities; V0.10 reports that capability but
    does not attempt to flatten unknown repeated/indexed fields without
    real-machine validation.
    """

    # These are runtime discovery probes, not claims that every PowerWorld build
    # uses these names. Only a schema that actually exposes the documented fields
    # is accepted.
    RESULT_OBJECT_CANDIDATES = [
        "VIOLATIONCTGINJECTIONSENSITIVITY",
        "VIOLATIONCTGINJSENS",
        "CTGINJECTIONSENSITIVITY",
        "CTGINJSENS",
    ]

    def __init__(self, adapter) -> None:
        self.adapter = adapter
        self.catalog = FieldCatalog(adapter)
        self.sensitivity = SensitivityEngine(adapter)

    @staticmethod
    def _num(v: Any) -> float | None:
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _injector_bus(text: str) -> int | None:
        m = re.search(r"\b(?:BUS\s*)?(\d+)\b", str(text), re.IGNORECASE)
        return int(m.group(1)) if m else None

    @staticmethod
    def _injector_type(text: str) -> str:
        s = str(text).upper()
        if "LOAD" in s:
            return "LOAD"
        if "GEN" in s or "GENERATOR" in s:
            return "GENERATOR"
        return "UNKNOWN"

    def _field_map(self, obj: str) -> dict[str, str] | None:
        try:
            fields = self.catalog.fields(obj)
        except Exception:
            return None
        if not fields:
            return None

        def choose(candidates: list[str], semantic: list[str] | None = None):
            value = self.catalog.choose(obj, candidates)
            if not value and semantic:
                value = self.catalog.find_semantic(obj, include=semantic)
            return value

        mapping = {
            "injector": choose(
                ["Injector", "InjectionObject", "Object"],
                ["injector"],
            ),
            "contingency": choose(
                ["Name", "CTGName", "CTGLabel", "Contingency"],
                ["contingency"],
            ),
            "element": choose(
                ["Element", "ElementFileFormat", "ViolElement"],
                ["element"],
            ),
            "sensitivity": choose(
                ["MWInjSensitivity", "MW Inj Sensitivity", "InjectionSensitivity"],
                ["mw", "inj", "sensitivity"],
            ),
            "range_inc": choose(
                ["MWRangeInc", "MW Range Inc"],
                ["mw", "range", "inc"],
            ),
            "range_dec": choose(
                ["MWRangeDec", "MW Range Dec"],
                ["mw", "range", "dec"],
            ),
            "effect_inc": choose(
                ["MWEffectInc", "MW Effect Inc"],
                ["mw", "effect", "inc"],
            ),
            "effect_dec": choose(
                ["MWEffectDec", "MW Effect Dec"],
                ["mw", "effect", "dec"],
            ),
        }
        if all(mapping[k] for k in ["injector", "contingency", "element", "sensitivity"]):
            return mapping
        return None

    def discover(self) -> dict[str, Any]:
        if not self.adapter.solver_backed:
            return {
                "status": "DEMO",
                "object_type": "DEMO_CTG_INJECTION_SENSITIVITY",
                "limitviol_embedded_fields": True,
            }

        for obj in self.RESULT_OBJECT_CANDIDATES:
            mapping = self._field_map(obj)
            if mapping:
                return {
                    "status": "DISCOVERED_FLAT_RESULT_OBJECT",
                    "object_type": obj,
                    "fields": mapping,
                    "limitviol_embedded_fields": True,
                }

        # The documented LimitViol display is the safe fallback signal.
        limit_fields = []
        try:
            for field in self.catalog.fields("LIMITVIOL"):
                hay = f"{field.variable} {field.description}".lower()
                if "sensitivity" in hay and (
                    "inject" in hay or "mw effect" in hay or "mw range" in hay
                ):
                    limit_fields.append({
                        "variable": field.variable,
                        "description": field.description,
                    })
        except Exception:
            pass

        return {
            "status": (
                "LIMITVIOL_EMBEDDED_FIELDS_ONLY"
                if limit_fields else "REAL_MACHINE_SCHEMA_NOT_DISCOVERED"
            ),
            "object_type": None,
            "limitviol_embedded_fields": bool(limit_fields),
            "limitviol_matching_fields": limit_fields[:50],
            "warning": (
                "PowerWorld documents injection-sensitivity fields on LimitViol, "
                "but V0.10 will not guess how repeated/indexed fields map to injectors "
                "until a real Simulator 24 machine exposes the actual schema."
            ),
        }

    def _demo_rows(self) -> list[InjectionSensitivityResult]:
        # Build records only for actual demo contingency violations.
        batch = NativeContingencyEngine(self.adapter).run_all()
        records: list[InjectionSensitivityResult] = []
        reference_bus = min(int(b["BusNum"]) for b in self.adapter.buses)

        gens = list(self.adapter.gens)
        loads = list(self.adapter.loads)

        for violation in batch.violations:
            nums = re.findall(r"\d+", violation.contingency)
            if len(nums) < 2:
                continue
            outage = BranchIdentity(
                int(nums[0]), int(nums[1]), nums[2] if len(nums) >= 3 else "1"
            )

            elem = re.search(
                r"(\d+)\s*[-–—]\s*(\d+)\s+([A-Za-z0-9&]+)",
                violation.object_id,
            )
            if not elem:
                continue
            monitored = BranchIdentity(
                int(elem.group(1)), int(elem.group(2)), elem.group(3)
            )

            for g in gens:
                bus = int(g["BusNum"])
                if bus == reference_bus:
                    sensitivity = 0.0
                else:
                    result = self.sensitivity.otdf(
                        source_bus=bus,
                        sink_bus=reference_bus,
                        monitored=monitored,
                        outage=outage,
                    )
                    sensitivity = float(result["otdf_pct"]) / 100.0

                inc = float(g["GenMWMax"]) - float(g["GenMW"])
                dec = float(g["GenMWMin"]) - float(g["GenMW"])  # signed negative
                records.append(InjectionSensitivityResult(
                    injector=f"GEN {bus} '{g['GenID']}'",
                    injector_type="GENERATOR",
                    injector_bus=bus,
                    contingency=violation.contingency,
                    element=f"BRANCH {monitored.from_bus} {monitored.to_bus} '{monitored.circuit}'",
                    mw_inj_sensitivity=sensitivity,
                    mw_range_inc=inc,
                    mw_range_dec=dec,
                    mw_effect_inc=inc * sensitivity,
                    mw_effect_dec=dec * sensitivity,
                    source="DEMO_OTDF_DERIVED",
                ))

            for ld in loads:
                bus = int(ld["BusNum"])
                if bus == reference_bus:
                    sensitivity = 0.0
                else:
                    result = self.sensitivity.otdf(
                        source_bus=bus,
                        sink_bus=reference_bus,
                        monitored=monitored,
                        outage=outage,
                    )
                    sensitivity = float(result["otdf_pct"]) / 100.0

                # Current PowerWorld help: increase in load injection is the
                # present load output; decrease in load injection is 0.
                inc = float(ld["LoadMW"])
                dec = 0.0
                records.append(InjectionSensitivityResult(
                    injector=f"LOAD {bus} '{ld['LoadID']}'",
                    injector_type="LOAD",
                    injector_bus=bus,
                    contingency=violation.contingency,
                    element=f"BRANCH {monitored.from_bus} {monitored.to_bus} '{monitored.circuit}'",
                    mw_inj_sensitivity=sensitivity,
                    mw_range_inc=inc,
                    mw_range_dec=dec,
                    mw_effect_inc=inc * sensitivity,
                    mw_effect_dec=0.0,
                    source="DEMO_OTDF_DERIVED",
                ))
        return records

    def rows(self) -> tuple[list[InjectionSensitivityResult], dict[str, Any]]:
        discovery = self.discover()

        if not self.adapter.solver_backed:
            return self._demo_rows(), discovery

        if discovery["status"] != "DISCOVERED_FLAT_RESULT_OBJECT":
            return [], discovery

        obj = discovery["object_type"]
        f = discovery["fields"]
        fields = [x for x in f.values() if x]
        raw = self.adapter.get_rows(obj, list(dict.fromkeys(fields)))

        result = []
        for row in raw:
            injector = str(row.get(f["injector"], ""))
            result.append(InjectionSensitivityResult(
                injector=injector,
                injector_type=self._injector_type(injector),
                injector_bus=self._injector_bus(injector),
                contingency=str(row.get(f["contingency"], "")),
                element=str(row.get(f["element"], "")),
                mw_inj_sensitivity=self._num(row.get(f["sensitivity"])),
                mw_range_inc=self._num(row.get(f["range_inc"])) if f.get("range_inc") else None,
                mw_range_dec=self._num(row.get(f["range_dec"])) if f.get("range_dec") else None,
                mw_effect_inc=self._num(row.get(f["effect_inc"])) if f.get("effect_inc") else None,
                mw_effect_dec=self._num(row.get(f["effect_dec"])) if f.get("effect_dec") else None,
                source=f"POWERWORLD:{obj}",
            ))
        return result, discovery

    @staticmethod
    def _element_matches(text: str, branch: BranchIdentity | None) -> bool:
        if branch is None:
            return True
        nums = re.findall(r"\d+", str(text))
        if len(nums) < 2:
            return False
        return {int(nums[0]), int(nums[1])} == {branch.from_bus, branch.to_bus}

    def rank_relief(
        self,
        *,
        contingency: str = "",
        violated_element: BranchIdentity | None = None,
        top_n: int = 20,
    ) -> dict[str, Any]:
        rows, discovery = self.rows()

        selected = []
        for row in rows:
            if contingency and row.contingency.lower() != contingency.lower():
                continue
            if not self._element_matches(row.element, violated_element):
                continue

            actions = []
            if row.mw_effect_inc is not None:
                actions.append(("INCREASE_INJECTION", row.mw_effect_inc, row.mw_range_inc))
            if row.mw_effect_dec is not None:
                actions.append(("DECREASE_INJECTION", row.mw_effect_dec, row.mw_range_dec))
            if not actions:
                continue

            action, effect, rng = min(actions, key=lambda x: x[1])

            if row.injector_type == "GENERATOR":
                action_plain = (
                    "INCREASE_GENERATION"
                    if action == "INCREASE_INJECTION"
                    else "DECREASE_GENERATION"
                )
            elif row.injector_type == "LOAD":
                # In PowerWorld's injection convention, increasing a load's
                # injection means moving its negative injection toward zero:
                # reducing load / demand response.
                action_plain = (
                    "REDUCE_LOAD"
                    if action == "INCREASE_INJECTION"
                    else "INCREASE_LOAD"
                )
            else:
                action_plain = action

            selected.append({
                **row.to_dict(),
                "best_action": action,
                "best_action_plain": action_plain,
                "best_mw_effect": effect,
                "best_available_range_mw": rng,
                "relief_rank_eligible": effect < 0,
            })

        selected.sort(
            key=lambda x: (
                not x["relief_rank_eligible"],
                x["best_mw_effect"] if x["best_mw_effect"] is not None else 1e30,
            )
        )

        return {
            "discovery": discovery,
            "contingency": contingency,
            "violated_element": (
                {
                    "from": violated_element.from_bus,
                    "to": violated_element.to_bus,
                    "circuit": violated_element.circuit,
                }
                if violated_element else None
            ),
            "results": selected[:top_n],
            "warning": (
                "PowerWorld's MW Effect is a violation-level screening result. "
                "Any recommended redispatch still requires a solved base-case and N-1 recheck."
            ),
        }
