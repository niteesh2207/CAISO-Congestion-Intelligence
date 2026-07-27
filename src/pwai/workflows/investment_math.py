from __future__ import annotations

from pathlib import Path
from typing import Any
import json

from ..resource_utils import project_or_package_resource


def capital_recovery_factor(rate: float, years: int) -> float:
    if years <= 0:
        raise ValueError("years must be positive.")
    if rate == 0:
        return 1.0 / years
    return rate * (1 + rate) ** years / ((1 + rate) ** years - 1)


def annualized_cost(
    capex: float,
    life_years: int,
    discount_rate: float,
    fixed_om_pct: float = 0.0,
) -> float:
    return (
        capex * capital_recovery_factor(discount_rate, life_years)
        + capex * fixed_om_pct
    )


def load_investment_assumptions(path: str | Path | None = None) -> dict[str, Any]:
    if path is None:
        path = project_or_package_resource("config", "investment_assumptions.json")
    return json.loads(Path(path).read_text(encoding="utf-8"))
