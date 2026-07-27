from __future__ import annotations

from dataclasses import dataclass
from typing import Any
import math
import re

from ..field_catalog import FieldCatalog
from .object_resolver import BranchIdentity
from .sensitivity import SensitivityEngine


def _number(value: Any) -> float | None:
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (TypeError, ValueError):
        return None


def parse_branch_reference(text: str | None) -> BranchIdentity | None:
    if not text:
        return None
    s = str(text)

    # Handles:
    # BRANCH 301 401 '1'
    # 301-401 1
    # 301 401 1
    patterns = [
        r"BRANCH\s+(\d+)\s+(\d+)\s+['\"]?([A-Za-z0-9&]+)['\"]?",
        r"(\d+)\s*[-–—]\s*(\d+)\s+['\"]?([A-Za-z0-9&]+)['\"]?",
        r"\b(\d+)\s+(\d+)\s+['\"]?([A-Za-z0-9&]+)['\"]?\b",
    ]
    for pattern in patterns:
        m = re.search(pattern, s, re.IGNORECASE)
        if m:
            return BranchIdentity(int(m.group(1)), int(m.group(2)), str(m.group(3)))
    return None


def parse_branch_from_contingency_label(name: str) -> BranchIdentity | None:
    # Common synthetic/simple contingency labels, e.g. L_301_401 or L_301_401_1.
    nums = re.findall(r"\d+", str(name))
    if len(nums) >= 2:
        ckt = nums[2] if len(nums) >= 3 else "1"
        return BranchIdentity(int(nums[0]), int(nums[1]), ckt)
    return None


@dataclass(frozen=True)
class SCOPFContingencyConstraint:
    contingency: str
    category: str
    element: str
    pre_optimization_value: float | None
    scaled_limit: float | None
    post_optimization_value: float | None
    error: float | None
    included: bool | None
    marginal_cost: float | None
    unenforceable: bool | None
    skip_violation: bool | None

    def to_dict(self) -> dict[str, Any]:
        return vars(self)


class SCOPFContingencyEconomics:
    """
    Reads the current SCOPF contingency-violation economic table.

    PowerWorld's current AUX format includes object type PWLPOPFCTGViol.
    Field names are still discovered dynamically because exported variable names
    can differ from GUI column labels.
    """

    OBJECT_TYPE = "PWLPOPFCTGViol"

    def __init__(self, adapter) -> None:
        self.adapter = adapter
        self.catalog = FieldCatalog(adapter)
        self.sensitivity = SensitivityEngine(adapter)

    @staticmethod
    def _bool(value: Any) -> bool | None:
        if value is None or str(value).strip() == "":
            return None
        return str(value).strip().upper() in {"YES", "TRUE", "1", "INCLUDED"}

    def _choose(
        self,
        candidates: list[str],
        include: list[str] | None = None,
        exclude: list[str] | None = None,
    ) -> str | None:
        field = self.catalog.choose(self.OBJECT_TYPE, candidates)
        if not field and include:
            field = self.catalog.find_semantic(
                self.OBJECT_TYPE,
                include=include,
                exclude=exclude or [],
            )
        return field

    def rows(self) -> tuple[list[SCOPFContingencyConstraint], list[str]]:
        warnings: list[str] = []

        try:
            fields_available = self.catalog.fields(self.OBJECT_TYPE)
        except Exception as exc:
            return [], [f"Could not access {self.OBJECT_TYPE}: {exc}"]
        if not fields_available:
            return [], [f"No {self.OBJECT_TYPE} result fields were available after SCOPF."]

        contingency = self._choose(
            ["CTGName", "CTGLabel", "ContingencyName", "Name"],
            ["contingency"],
        ) or self._choose(["Name"], ["name"])
        category = self._choose(["Category", "ViolType"], ["category"])
        element = self._choose(
            ["Element", "ViolElement", "ElementFileFormat"],
            ["element"],
        )
        value = self._choose(
            ["Value", "InitialValue", "ViolValue"],
            ["value"],
            ["new", "limit", "error"],
        )
        scaled_limit = self._choose(
            ["ScaledLimit", "Limit"],
            ["scaled", "limit"],
        )
        new_value = self._choose(
            ["NewValue", "PostValue"],
            ["new", "value"],
        )
        error = self._choose(["Error"], ["error"])
        included = self._choose(["Included", "Include"], ["included"])
        marginal = self._choose(
            ["MarginalCost", "MargCost", "Lambda"],
            ["marginal", "cost"],
        )
        unenforceable = self._choose(
            ["Unenforceable"],
            ["unenforceable"],
        )
        skip = self._choose(
            ["SkipViolation", "Skip"],
            ["skip"],
        )

        required = {
            "contingency": contingency,
            "category": category,
            "element": element,
        }
        missing_required = [k for k, v in required.items() if not v]
        if missing_required:
            return [], [
                "Could not resolve required SCOPF contingency-constraint fields: "
                + ", ".join(missing_required)
            ]

        fields = [
            contingency, category, element, value, scaled_limit, new_value,
            error, included, marginal, unenforceable, skip,
        ]
        fields = [x for x in fields if x]
        raw = self.adapter.get_rows(self.OBJECT_TYPE, list(dict.fromkeys(fields)))

        result = []
        for row in raw:
            result.append(SCOPFContingencyConstraint(
                contingency=str(row.get(contingency, "")),
                category=str(row.get(category, "")),
                element=str(row.get(element, "")),
                pre_optimization_value=_number(row.get(value)) if value else None,
                scaled_limit=_number(row.get(scaled_limit)) if scaled_limit else None,
                post_optimization_value=_number(row.get(new_value)) if new_value else None,
                error=_number(row.get(error)) if error else None,
                included=self._bool(row.get(included)) if included else None,
                marginal_cost=_number(row.get(marginal)) if marginal else None,
                unenforceable=self._bool(row.get(unenforceable)) if unenforceable else None,
                skip_violation=self._bool(row.get(skip)) if skip else None,
            ))

        if not marginal:
            warnings.append(
                "SCOPF contingency marginal-cost field was not resolved; economic attribution is incomplete."
            )
        if not new_value or not scaled_limit:
            warnings.append(
                "SCOPF post-optimization value / scaled-limit fields were incomplete."
            )
        return result, warnings

    def rank(self, rows: list[SCOPFContingencyConstraint]) -> list[dict[str, Any]]:
        ranked = [r.to_dict() for r in rows]
        ranked.sort(
            key=lambda r: abs(float(r["marginal_cost"] or 0.0)),
            reverse=True,
        )
        return ranked

    def source_sink_exposure(
        self,
        rows: list[SCOPFContingencyConstraint],
        *,
        source_bus: int,
        sink_bus: int,
    ) -> list[dict[str, Any]]:
        """
        Rank economically active contingency constraints for a source→sink transfer.

        Screening metric:
            signed exposure = marginal_cost * OTDF / 100

        This is not presented as exact bus-LMP contribution.
        """
        result = []
        for row in rows:
            monitored = parse_branch_reference(row.element)
            outage = parse_branch_from_contingency_label(row.contingency)
            if not monitored or not outage or row.marginal_cost is None:
                continue
            try:
                otdf = self.sensitivity.otdf(
                    source_bus=source_bus,
                    sink_bus=sink_bus,
                    monitored=monitored,
                    outage=outage,
                )
            except Exception:
                continue

            signed = float(row.marginal_cost) * float(otdf["otdf_pct"]) / 100.0
            result.append({
                "contingency": row.contingency,
                "monitored_element": row.element,
                "marginal_cost": row.marginal_cost,
                "otdf_pct": otdf["otdf_pct"],
                "signed_security_exposure_screen": signed,
                "absolute_security_exposure_screen": abs(signed),
                "interpretation": "SCREENING_SIGNAL_NOT_EXACT_LMP_CONTRIBUTION",
            })

        result.sort(
            key=lambda x: x["absolute_security_exposure_screen"],
            reverse=True,
        )
        return result
