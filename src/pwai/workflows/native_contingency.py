from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..field_catalog import FieldCatalog
from .model_doctor import ModelDoctor
from .object_resolver import BranchIdentity
from .sensitivity import SensitivityEngine


@dataclass(frozen=True)
class ContingencyStatus:
    name: str
    skip: bool
    processed: bool
    solved: str


@dataclass(frozen=True)
class ContingencyViolation:
    contingency: str
    object_type: str
    object_id: str
    category: str
    value: float | None
    limit: float | None
    percent: float | None

    @property
    def signature(self) -> tuple[str, str, str, str]:
        return (
            self.contingency,
            self.object_type,
            self.object_id,
            self.category,
        )


@dataclass
class ContingencyBatchResult:
    contingencies: list[ContingencyStatus] = field(default_factory=list)
    violations: list[ContingencyViolation] = field(default_factory=list)
    commands: list[str] = field(default_factory=list)
    result_source: str = "UNKNOWN"

    @property
    def processed_count(self) -> int:
        return sum(1 for c in self.contingencies if c.processed)

    @property
    def unsolved_count(self) -> int:
        return sum(
            1 for c in self.contingencies
            if c.processed and c.solved.upper() not in {"YES", "SOLVED", "TRUE", "1"}
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "contingencies": [vars(c) for c in self.contingencies],
            "violations": [vars(v) for v in self.violations],
            "processed_count": self.processed_count,
            "unsolved_count": self.unsolved_count,
            "violation_count": len(self.violations),
            "commands": list(self.commands),
            "result_source": self.result_source,
        }


class NativeContingencyEngine:
    """
    PowerWorld-native contingency batch wrapper.

    Real mode:
      EnterMode(Contingency)
      CTGSetAsReference
      CTGSolveAll(NO, YES)
      read Contingency + ViolationCTG objects
      CTGRestoreReference

    Demo mode:
      Uses a deterministic DC/LODF contingency set so the product can exercise
      N-1 comparison logic without claiming PowerWorld results.
    """

    def __init__(self, adapter) -> None:
        self.adapter = adapter
        self.catalog = FieldCatalog(adapter)
        self.doctor = ModelDoctor(adapter)
        self.sensitivity = SensitivityEngine(adapter)

    @staticmethod
    def _bool(value: Any) -> bool:
        return str(value).strip().upper() in {"YES", "TRUE", "1", "CLOSED"}

    def _choose(
        self,
        obj: str,
        candidates: list[str],
        semantic: list[str] | None = None,
        *,
        required: bool = False,
    ) -> str | None:
        field = self.catalog.choose(obj, candidates)
        if not field and semantic:
            field = self.catalog.find_semantic(obj, include=semantic)
        if required and not field:
            raise RuntimeError(
                f"Could not resolve required {obj} field from {candidates}."
            )
        return field

    def _real_contingencies(self) -> list[ContingencyStatus]:
        name = self._choose(
            "CONTINGENCY", ["CTGLabel", "Label", "Name"],
            ["label"], required=True
        )
        skip = self._choose("CONTINGENCY", ["CTGSkip", "Skip"], ["skip"])
        processed = self._choose(
            "CONTINGENCY", ["CTGProcessed", "Processed"], ["processed"]
        )
        solved = self._choose(
            "CONTINGENCY", ["CTGSolved", "Solved"], ["solved"]
        )

        fields = [name] + [x for x in (skip, processed, solved) if x]
        rows = self.adapter.get_rows("CONTINGENCY", fields)
        result = []
        for row in rows:
            result.append(ContingencyStatus(
                name=str(row.get(name, "")),
                skip=self._bool(row.get(skip)) if skip else False,
                processed=self._bool(row.get(processed)) if processed else True,
                solved=str(row.get(solved, "UNKNOWN")) if solved else "UNKNOWN",
            ))
        return result

    def _real_violations(self) -> list[ContingencyViolation]:
        obj = "VIOLATIONCTG"

        ctg = self._choose(obj, ["CTGLabel", "Contingency"], ["ctg"], required=True)
        cat = self._choose(
            obj, ["LimViolCat", "Category"], ["viol", "cat"], required=True
        )
        pct = self._choose(
            obj, ["LimViolPct", "Percent"], ["viol", "pct"]
        )
        value = self._choose(
            obj, ["LimViolValue", "Value"], ["viol", "value"]
        )
        limit = self._choose(
            obj, ["LimViolLimit", "Limit"], ["viol", "limit"]
        )

        bus1 = self._choose(obj, ["BusNum"])
        bus2 = self._choose(obj, ["BusNum:1"])
        circuit = self._choose(obj, ["LineCircuit"])
        object_type = self._choose(
            obj, ["ObjectType", "LimViolObjectType"], ["object", "type"]
        )
        object_text = self._choose(
            obj, ["Object", "LimViolObject"], ["object"]
        )

        fields = [ctg, cat] + [
            x for x in (pct, value, limit, bus1, bus2, circuit, object_type, object_text)
            if x
        ]
        rows = self.adapter.get_rows(obj, list(dict.fromkeys(fields)))
        result: list[ContingencyViolation] = []

        for row in rows:
            typ = str(row.get(object_type, "") or "") if object_type else ""
            if not typ:
                cat_text = str(row.get(cat, ""))
                typ = "BRANCH" if "BRANCH" in cat_text.upper() else "UNKNOWN"

            if bus1 and bus2 and row.get(bus1) not in (None, "") and row.get(bus2) not in (None, ""):
                ident = f"{row.get(bus1)}-{row.get(bus2)}"
                if circuit and row.get(circuit) not in (None, ""):
                    ident += f" {row.get(circuit)}"
            elif object_text:
                ident = str(row.get(object_text, ""))
            else:
                ident = "UNKNOWN"

            def number(field):
                if not field:
                    return None
                try:
                    return float(row.get(field))
                except (TypeError, ValueError):
                    return None

            result.append(ContingencyViolation(
                contingency=str(row.get(ctg, "")),
                object_type=typ,
                object_id=ident,
                category=str(row.get(cat, "")),
                value=number(value),
                limit=number(limit),
                percent=number(pct),
            ))
        return result

    def _demo_contingencies(self) -> list[tuple[str, BranchIdentity]]:
        return [
            ("L_101_301", BranchIdentity(101, 301, "1")),
            ("L_201_301", BranchIdentity(201, 301, "1")),
            ("L_301_401", BranchIdentity(301, 401, "1")),
            ("L_301_501", BranchIdentity(301, 501, "1")),
            ("L_401_501", BranchIdentity(401, 501, "1")),
        ]

    def _demo_run(self) -> ContingencyBatchResult:
        base = self.doctor.branch_snapshot()
        result = ContingencyBatchResult(result_source="DEMO_DC_LODF")

        for name, outage in self._demo_contingencies():
            try:
                outaged = next(
                    row for row in base
                    if {
                        int(row["from"]), int(row["to"])
                    } == {outage.from_bus, outage.to_bus}
                    and str(row["circuit"]) == outage.circuit
                )
                lodfs = self.sensitivity.lodf(outage)
                result.contingencies.append(
                    ContingencyStatus(
                        name=name, skip=False, processed=True, solved="YES"
                    )
                )

                for row in base:
                    if {
                        int(row["from"]), int(row["to"])
                    } == {outage.from_bus, outage.to_bus} and str(row["circuit"]) == outage.circuit:
                        continue

                    lodf = next(
                        (
                            x["lodf_pct"] for x in lodfs
                            if {
                                int(x["from"]), int(x["to"])
                            } == {int(row["from"]), int(row["to"])}
                            and str(x["circuit"]) == str(row["circuit"])
                        ),
                        None,
                    )
                    if lodf is None or row.get("mw") is None or outaged.get("mw") is None:
                        continue

                    post_mw = float(row["mw"]) + (float(lodf) / 100.0) * float(outaged["mw"])
                    normal_limit = row.get("limit_mva")
                    if normal_limit in (None, 0):
                        continue

                    # Synthetic emergency ratings for the demo contingency study.
                    # These are intentionally separate from the normal/base ratings.
                    # They keep the demo internally useful without changing the
                    # Model Doctor/base-case overload behavior.
                    key = (
                        min(int(row["from"]), int(row["to"])),
                        max(int(row["from"]), int(row["to"])),
                        str(row["circuit"]),
                    )
                    emergency_limits = {
                        (101, 201, "1"): 1200.0,
                        (101, 301, "1"): 1100.0,
                        (201, 301, "1"): 970.0,
                        (301, 401, "1"): 800.0,
                        (301, 501, "1"): 800.0,
                        (401, 501, "1"): 550.0,
                    }
                    limit = emergency_limits.get(key, float(normal_limit))
                    post_mva = abs(post_mw) * 1.045
                    pct = 100.0 * post_mva / float(limit)

                    if pct >= 100.0:
                        result.violations.append(
                            ContingencyViolation(
                                contingency=name,
                                object_type="BRANCH",
                                object_id=f"{row['from']}-{row['to']} {row['circuit']}",
                                category="Branch MVA",
                                value=post_mva,
                                limit=float(limit),
                                percent=pct,
                            )
                        )
            except Exception:
                result.contingencies.append(
                    ContingencyStatus(
                        name=name, skip=False, processed=True, solved="NO"
                    )
                )

        return result

    def run_all(self) -> ContingencyBatchResult:
        if not self.adapter.solver_backed:
            return self._demo_run()

        commands = [
            "EnterMode(Contingency);",
            "CTGSetAsReference;",
            "CTGSolveAll(NO, YES);",
        ]
        for command in commands:
            self.adapter.run_script(command)

        try:
            contingencies = self._real_contingencies()
            violations = self._real_violations()
        finally:
            self.adapter.run_script("CTGRestoreReference;")

        return ContingencyBatchResult(
            contingencies=contingencies,
            violations=violations,
            commands=[*commands, "CTGRestoreReference;"],
            result_source="POWERWORLD_NATIVE_CONTINGENCY",
        )
