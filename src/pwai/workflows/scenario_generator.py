from __future__ import annotations

from itertools import product
from typing import Any
import json

from ..resource_utils import project_or_package_resource


class AutomaticScenarioGenerator:
    """
    Generates and screens combinatorial grid scenarios.

    The generator is intentionally transparent: risk score is a screening score,
    not a probability or solved PowerWorld security result.
    """

    def _config(self) -> dict[str, Any]:
        p = project_or_package_resource("config", "scenario_generator.json")
        return json.loads(p.read_text(encoding="utf-8"))

    @staticmethod
    def _score(parts: list[dict[str, Any]]) -> float:
        # Transparent weighted severity screen. Order is:
        # load, weather, outage, BESS SOC.
        weights = [0.25, 0.20, 0.35, 0.20]
        values = [
            max(0.0, min(1.0, float(part.get("risk", 0.0))))
            for part in parts
        ]
        return sum(w * v for w, v in zip(weights, values))

    def generate(self) -> dict[str, Any]:
        cfg = self._config()
        dims = cfg["dimensions"]
        names = list(dims)
        combos = []

        for values in product(*(dims[name] for name in names)):
            parts = list(values)
            scenario = {name: value for name, value in zip(names, values)}
            score = self._score(parts)

            # Extra interaction penalties/rewards make the screen more useful
            # without pretending to be a probability model.
            load_name = scenario["load"]["name"]
            weather_name = scenario["weather"]["name"]
            outage_name = scenario["outage"]["name"]
            soc_name = scenario["bess_soc"]["name"]

            interaction = 0.0
            if load_name == "HIGH_LOAD" and weather_name == "HOT_LOW_WIND":
                interaction += 0.12
            if outage_name != "NONE" and load_name == "HIGH_LOAD":
                interaction += 0.10
            if outage_name != "NONE" and soc_name == "LOW_SOC":
                interaction += 0.08

            score = min(1.0, score + interaction)
            combos.append({
                "scenario_id": (
                    f"{load_name}|{weather_name}|{outage_name}|{soc_name}"
                ),
                "screening_score": score,
                "inputs": scenario,
                "recommended_solution_depth": (
                    "FULL_AC_PLUS_N1"
                    if score >= 0.90
                    else "AC_POWER_FLOW"
                    if score >= 0.75
                    else "LINEAR_SCREEN"
                ),
            })

        combos.sort(key=lambda x: x["screening_score"], reverse=True)
        retained = combos[: int(cfg.get("max_retained", 24))]

        counts = {
            "FULL_AC_PLUS_N1": sum(
                1 for x in retained
                if x["recommended_solution_depth"] == "FULL_AC_PLUS_N1"
            ),
            "AC_POWER_FLOW": sum(
                1 for x in retained
                if x["recommended_solution_depth"] == "AC_POWER_FLOW"
            ),
            "LINEAR_SCREEN": sum(
                1 for x in retained
                if x["recommended_solution_depth"] == "LINEAR_SCREEN"
            ),
        }

        return {
            "theoretical_scenarios": len(combos),
            "retained_scenarios": len(retained),
            "retained": retained,
            "solution_depth_counts": counts,
            "provenance": cfg["provenance"],
            "screening_method": "TRANSPARENT_MULTI_FACTOR_SEVERITY_SCREEN",
            "next_step": (
                "Feed retained scenarios into PowerWorld linear screening, "
                "then promote critical cases to full AC/N-1/OPF/SCOPF as licensed."
            ),
            "guardrail": (
                "Screening score is not a statistical probability and is not "
                "a PowerWorld solved security metric."
            ),
        }
