from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any
import json
import math

from ..resource_utils import project_or_package_resource

from .storage_portfolio import StoragePortfolioOptimizer
from .time_series_scenario import load_scenario, PortfolioScenario, TimePoint


def _quantile(values: list[float], q: float) -> float:
    if not values:
        return float("nan")
    xs = sorted(values)
    if len(xs) == 1:
        return xs[0]
    pos = (len(xs)-1)*q
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return xs[lo]
    w = pos-lo
    return xs[lo]*(1-w)+xs[hi]*w


class ScenarioEnsembleEngine:
    def __init__(self, adapter, config_path: str | Path | None = None) -> None:
        self.adapter = adapter
        if config_path is None:
            config_path = project_or_package_resource("config", "scenario_ensemble.json")
        self.config = json.loads(Path(config_path).read_text(encoding="utf-8"))

    def _scenario(self, row: dict[str, Any]) -> PortfolioScenario:
        base = load_scenario()
        points = [
            TimePoint(
                timestamp=tp.timestamp,
                duration_hours=tp.duration_hours,
                load_multiplier=tp.load_multiplier*float(row["load_scale"]),
                energy_price_per_mwh=tp.energy_price_per_mwh*float(row["price_scale"]),
                required_relief_mw=tp.required_relief_mw*float(row["relief_scale"]),
            )
            for tp in base.timepoints
        ]
        return PortfolioScenario(
            name=f"{base.name}:{row['name']}",
            monitored=base.monitored,
            outage=base.outage,
            reference_bus=base.reference_bus,
            balancing_bus=base.balancing_bus,
            balancing_gen_id=base.balancing_gen_id,
            action_step_mw=base.action_step_mw,
            throughput_cost_per_mwh=base.throughput_cost_per_mwh,
            unserved_relief_penalty_per_mwh=base.unserved_relief_penalty_per_mwh,
            terminal_soc_target_pct=base.terminal_soc_target_pct,
            terminal_soc_penalty_per_pct=base.terminal_soc_penalty_per_pct,
            timepoints=points,
            provenance=self.config.get("provenance", "UNKNOWN"),
        )

    def run(self) -> dict[str, Any]:
        rows = []
        total_p = 0.0
        for cfg in self.config["scenarios"]:
            probability = float(cfg["probability"])
            total_p += probability
            scenario = self._scenario(cfg)
            result = StoragePortfolioOptimizer(self.adapter).optimize(scenario)
            rows.append({
                "name": cfg["name"],
                "probability": probability,
                "load_scale": cfg["load_scale"],
                "price_scale": cfg["price_scale"],
                "relief_scale": cfg["relief_scale"],
                "objective_value": result["objective_value"],
                "unserved_relief_mwh": result["portfolio_metrics"]["unserved_relief_mwh"],
                "throughput_mwh": result["portfolio_metrics"]["throughput_mwh"],
                "terminal_soc": result["terminal_soc"],
            })

        if abs(total_p-1.0) > 1e-6:
            raise RuntimeError(f"Scenario probabilities sum to {total_p}, not 1.0.")

        expected_obj = sum(r["probability"]*r["objective_value"] for r in rows)
        expected_unserved = sum(
            r["probability"]*r["unserved_relief_mwh"] for r in rows
        )
        probability_shortfall = sum(
            r["probability"] for r in rows if r["unserved_relief_mwh"] > 1e-6
        )

        objs = [float(r["objective_value"]) for r in rows]
        q80 = _quantile(objs, 0.80)
        tail = [r for r in rows if r["objective_value"] >= q80]
        tail_weight = sum(r["probability"] for r in tail)
        cvar80 = (
            sum(r["probability"]*r["objective_value"] for r in tail)/tail_weight
            if tail_weight else q80
        )

        return {
            "provenance": self.config.get("provenance"),
            "scenarios": rows,
            "risk": {
                "expected_objective": expected_obj,
                "expected_unserved_relief_mwh": expected_unserved,
                "probability_any_relief_shortfall": probability_shortfall,
                "objective_p80": q80,
                "objective_cvar80_discrete": cvar80,
                "worst_case_scenario": max(rows, key=lambda r: r["objective_value"])["name"],
                "worst_unserved_relief_scenario": max(
                    rows, key=lambda r: r["unserved_relief_mwh"]
                )["name"],
            },
            "guardrails": [
                "SYNTHETIC_ENSEMBLE_UNTIL_INPUT_PROVENANCE_VERIFIED",
                "DISCRETE_SCENARIO_RISK_NOT_MONTE_CARLO_CERTIFICATION",
            ],
        }
