from __future__ import annotations

from typing import Any
import json

from ..field_catalog import FieldCatalog
from ..resource_utils import project_or_package_resource
from .model_doctor import ModelDoctor


class WeatherDLRIntelligence:
    """
    Weather-aware grid capacity layer.

    Real PowerWorld mode relies on PowerWorld's configured WeatherStation /
    weather-dependent-limit models. Demo mode applies explicit synthetic rating
    multipliers solely for product testing.
    """

    def __init__(self, adapter) -> None:
        self.adapter = adapter
        self.catalog = FieldCatalog(adapter)
        self.doctor = ModelDoctor(adapter)

    def _config(self) -> dict[str, Any]:
        p = project_or_package_resource("config", "weather_scenario.json")
        return json.loads(p.read_text(encoding="utf-8"))

    def discover_native_weather(self) -> dict[str, Any]:
        objects = {}
        for obj in ["WEATHERSTATION", "WEATHERMODEL", "BRANCH", "GEN"]:
            try:
                fields = self.catalog.fields(obj)
            except Exception:
                fields = []
            matched = [
                {
                    "variable": f.variable,
                    "description": f.description,
                }
                for f in fields
                if any(
                    term in f"{f.variable} {f.description}".lower()
                    for term in [
                        "weather", "temperature", "wind", "rating",
                        "limit", "mwmax", "mwmin",
                    ]
                )
            ]
            if matched:
                objects[obj] = matched[:100]
        return {
            "objects": objects,
            "powerworld_native_weather_expected": self.adapter.solver_backed,
            "warning": (
                "The product does not calculate a conductor DLR from weather unless "
                "the user supplies an approved thermal-rating model. Native configured "
                "PowerWorld weather-dependent limits remain authoritative."
            ),
        }

    def evaluate_demo(self, scenario_name: str = "HOT_LOW_WIND") -> dict[str, Any]:
        cfg = self._config()
        scenario = cfg["scenarios"].get(scenario_name.upper())
        if scenario is None:
            raise RuntimeError(f"Unknown weather scenario {scenario_name}.")

        if self.adapter.solver_backed:
            return {
                "mode": "POWERWORLD_NATIVE_WEATHER",
                "scenario_requested": scenario_name,
                "native_discovery": self.discover_native_weather(),
                "warning": (
                    "Synthetic multipliers are not applied to a real PowerWorld case. "
                    "Use the case's configured WeatherStation/weather-dependent-limit models."
                ),
            }

        f = self.doctor.branch_fields()
        if not f["limit"]:
            raise RuntimeError("Branch MVA-limit field is unavailable.")

        base = self.doctor.branch_snapshot()

        gen_changes = []
        gen_fields = {
            "bus": self.catalog.choose("GEN", ["BusNum"]),
            "id": self.catalog.choose("GEN", ["GenID", "ID"]),
            "max": self.catalog.choose("GEN", ["GenMWMax", "GenMaxMW", "MaxMW"]),
        }
        base_gen = []
        if all(gen_fields.values()):
            base_gen = self.adapter.get_rows(
                "GEN", [gen_fields["bus"], gen_fields["id"], gen_fields["max"]]
            )

        self.adapter.save_state()
        try:
            for row in base:
                key = f"{row['from']}-{row['to']}-{row['circuit']}"
                multiplier = float(
                    scenario["branch_rating_multiplier"].get(key, 1.0)
                )
                if row["limit_mva"] is not None and multiplier != 1.0:
                    self.adapter.change_single(
                        "BRANCH",
                        [f["from"], f["to"], f["circuit"], f["limit"]],
                        [
                            row["from"], row["to"], row["circuit"],
                            float(row["limit_mva"]) * multiplier,
                        ],
                    )
            if all(gen_fields.values()):
                for row in base_gen:
                    key = f"{int(row[gen_fields['bus']])}/{str(row[gen_fields['id']])}"
                    multiplier = float(
                        scenario["generator_max_multiplier"].get(key, 1.0)
                    )
                    base_max = float(row[gen_fields["max"]])
                    target_max = base_max * multiplier
                    if multiplier != 1.0:
                        self.adapter.change_single(
                            "GEN",
                            [gen_fields["bus"], gen_fields["id"], gen_fields["max"]],
                            [
                                int(row[gen_fields["bus"]]),
                                str(row[gen_fields["id"]]),
                                target_max,
                            ],
                        )
                    gen_changes.append({
                        "generator": key,
                        "base_max_mw": base_max,
                        "weather_max_mw": target_max,
                        "multiplier": multiplier,
                    })

            self.adapter.run_script("EnterMode(PowerFlow); SolvePowerFlow(RECTNEWT);")
            post = self.doctor.branch_snapshot()
        finally:
            self.adapter.load_state()

        changes = []
        base_map = {
            (int(r["from"]), int(r["to"]), str(r["circuit"])): r for r in base
        }
        for row in post:
            key = (int(row["from"]), int(row["to"]), str(row["circuit"]))
            b = base_map[key]
            changes.append({
                "branch": f"{row['from']}-{row['to']} {row['circuit']}",
                "base_limit_mva": b["limit_mva"],
                "weather_limit_mva": row["limit_mva"],
                "base_loading_pct": b["loading_pct"],
                "weather_loading_pct": row["loading_pct"],
                "delta_loading_pct_points": (
                    float(row["loading_pct"]) - float(b["loading_pct"])
                    if row["loading_pct"] is not None and b["loading_pct"] is not None
                    else None
                ),
            })
        changes.sort(
            key=lambda r: (
                r["weather_loading_pct"]
                if r["weather_loading_pct"] is not None else -1
            ),
            reverse=True,
        )

        return {
            "mode": "DEMO_SYNTHETIC_WEATHER_RATING",
            "scenario": scenario_name.upper(),
            "weather": scenario,
            "branch_changes": changes,
            "generator_capability_changes": gen_changes,
            "state_restored": True,
            "provenance": cfg["provenance"],
        }
