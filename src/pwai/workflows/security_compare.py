from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .native_contingency import ContingencyBatchResult, ContingencyViolation


@dataclass
class SecurityComparison:
    pass_security: bool
    baseline_contingencies: int
    candidate_contingencies: int
    baseline_unsolved: int
    candidate_unsolved: int
    new_unsolved: list[str] = field(default_factory=list)
    new_violations: list[dict[str, Any]] = field(default_factory=list)
    worsened_violations: list[dict[str, Any]] = field(default_factory=list)
    improved_violations: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "pass_security": self.pass_security,
            "baseline_contingencies": self.baseline_contingencies,
            "candidate_contingencies": self.candidate_contingencies,
            "baseline_unsolved": self.baseline_unsolved,
            "candidate_unsolved": self.candidate_unsolved,
            "new_unsolved": self.new_unsolved,
            "new_violations": self.new_violations,
            "worsened_violations": self.worsened_violations,
            "improved_violations": self.improved_violations,
        }


def _pct(v: ContingencyViolation) -> float:
    return float(v.percent) if v.percent is not None else 0.0


def compare_security(
    baseline: ContingencyBatchResult,
    candidate: ContingencyBatchResult,
    *,
    worsening_tolerance_pct_points: float = 5.0,
) -> SecurityComparison:
    base_status = {c.name: c for c in baseline.contingencies}
    cand_status = {c.name: c for c in candidate.contingencies}

    new_unsolved = []
    for name, c in cand_status.items():
        cand_solved = c.solved.upper() in {"YES", "SOLVED", "TRUE", "1"}
        b = base_status.get(name)
        base_solved = (
            b is not None
            and b.solved.upper() in {"YES", "SOLVED", "TRUE", "1"}
        )
        if not cand_solved and base_solved:
            new_unsolved.append(name)

    base_map = {v.signature: v for v in baseline.violations}
    cand_map = {v.signature: v for v in candidate.violations}

    new_violations = []
    worsened = []
    improved = []

    for sig, cv in cand_map.items():
        bv = base_map.get(sig)
        if bv is None:
            new_violations.append(vars(cv))
            continue
        delta = _pct(cv) - _pct(bv)
        if delta > worsening_tolerance_pct_points:
            worsened.append({
                "signature": sig,
                "baseline_percent": bv.percent,
                "candidate_percent": cv.percent,
                "delta_pct_points": delta,
            })
        elif delta < -worsening_tolerance_pct_points:
            improved.append({
                "signature": sig,
                "baseline_percent": bv.percent,
                "candidate_percent": cv.percent,
                "delta_pct_points": delta,
            })

    # Violations removed entirely are improvements.
    for sig, bv in base_map.items():
        if sig not in cand_map:
            improved.append({
                "signature": sig,
                "baseline_percent": bv.percent,
                "candidate_percent": None,
                "delta_pct_points": None,
                "resolved": True,
            })

    available = bool(candidate.contingencies)
    passed = (
        available
        and not new_unsolved
        and not new_violations
        and not worsened
    )

    return SecurityComparison(
        pass_security=passed,
        baseline_contingencies=len(baseline.contingencies),
        candidate_contingencies=len(candidate.contingencies),
        baseline_unsolved=baseline.unsolved_count,
        candidate_unsolved=candidate.unsolved_count,
        new_unsolved=new_unsolved,
        new_violations=new_violations,
        worsened_violations=worsened,
        improved_violations=improved,
    )
