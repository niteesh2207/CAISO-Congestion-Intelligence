from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
import json

from ..resource_utils import project_or_package_resource

from .object_resolver import BranchIdentity


@dataclass(frozen=True)
class TimePoint:
    timestamp: str
    duration_hours: float
    load_multiplier: float
    energy_price_per_mwh: float
    required_relief_mw: float


@dataclass(frozen=True)
class PortfolioScenario:
    name: str
    monitored: BranchIdentity
    outage: BranchIdentity
    reference_bus: int
    balancing_bus: int
    balancing_gen_id: str
    action_step_mw: float
    throughput_cost_per_mwh: float
    unserved_relief_penalty_per_mwh: float
    terminal_soc_target_pct: dict[str, float]
    terminal_soc_penalty_per_pct: float
    timepoints: list[TimePoint]
    provenance: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "monitored": vars(self.monitored),
            "outage": vars(self.outage),
            "reference_bus": self.reference_bus,
            "balancing_generator": {
                "bus": self.balancing_bus,
                "id": self.balancing_gen_id,
            },
            "action_step_mw": self.action_step_mw,
            "throughput_cost_per_mwh": self.throughput_cost_per_mwh,
            "unserved_relief_penalty_per_mwh": self.unserved_relief_penalty_per_mwh,
            "terminal_soc_target_pct": self.terminal_soc_target_pct,
            "terminal_soc_penalty_per_pct": self.terminal_soc_penalty_per_pct,
            "timepoints": [vars(x) for x in self.timepoints],
            "provenance": self.provenance,
        }


def load_scenario(path: str | Path | None = None) -> PortfolioScenario:
    if path is None:
        path = project_or_package_resource("config", "time_series_scenario.json")
    path = Path(path)
    data = json.loads(path.read_text(encoding="utf-8"))

    mon = data["monitored_branch"]
    out = data["outage_branch"]
    bal = data["balancing_generator"]
    points = [
        TimePoint(
            timestamp=str(row["timestamp"]),
            duration_hours=float(row["duration_hours"]),
            load_multiplier=float(row["load_multiplier"]),
            energy_price_per_mwh=float(row["energy_price_per_mwh"]),
            required_relief_mw=float(row["required_relief_mw"]),
        )
        for row in data["timepoints"]
    ]
    return PortfolioScenario(
        name=str(data["name"]),
        monitored=BranchIdentity(int(mon["from"]), int(mon["to"]), str(mon.get("circuit", "1"))),
        outage=BranchIdentity(int(out["from"]), int(out["to"]), str(out.get("circuit", "1"))),
        reference_bus=int(data["reference_bus"]),
        balancing_bus=int(bal["bus"]),
        balancing_gen_id=str(bal["id"]),
        action_step_mw=float(data.get("action_step_mw", 50.0)),
        throughput_cost_per_mwh=float(data.get("throughput_cost_per_mwh", 0.0)),
        unserved_relief_penalty_per_mwh=float(data.get("unserved_relief_penalty_per_mwh", 100.0)),
        terminal_soc_target_pct={
            str(k): float(v)
            for k, v in data.get("terminal_soc_target_pct", {}).items()
        },
        terminal_soc_penalty_per_pct=float(data.get("terminal_soc_penalty_per_pct", 0.0)),
        timepoints=points,
        provenance="SYNTHETIC_DEMO_SCENARIO" if "DEMO" in str(data.get("name", "")).upper() else "USER_SCENARIO",
    )
