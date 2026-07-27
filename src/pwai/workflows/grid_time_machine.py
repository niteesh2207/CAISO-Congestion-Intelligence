from __future__ import annotations

from typing import Any

from ..field_catalog import FieldCatalog
from .generator_controls import GeneratorInventory
from .model_doctor import ModelDoctor
from .native_contingency import NativeContingencyEngine
from .storage_inventory import StorageInventory
from .time_series_scenario import PortfolioScenario


class GridTimeMachine:
    """
    Protected hour-by-hour replay of a product-owned storage schedule.

    Each timepoint:
    - restores the original case state;
    - applies timepoint load multiplier;
    - applies existing BA-unit MW setpoints;
    - balances total MW with one explicit generator;
    - solves power flow;
    - records monitored branch state and N-1 summary.

    This is deliberately separate from native PowerWorld TSS. Native TSS can be
    executed through TimeStepBridge when the user's case already contains TSS
    timepoints/input data.
    """

    def __init__(self, adapter) -> None:
        self.adapter = adapter
        self.catalog = FieldCatalog(adapter)
        self.gens = GeneratorInventory(adapter)
        self.storage = StorageInventory(adapter)
        self.doctor = ModelDoctor(adapter)

    def _load_field_map(self) -> dict[str, str]:
        def req(candidates, semantic):
            f = self.catalog.choose("LOAD", candidates)
            if not f:
                f = self.catalog.find_semantic("LOAD", include=semantic)
            if not f:
                raise RuntimeError(f"Could not resolve LOAD field {candidates}.")
            return f
        return {
            "bus": req(["BusNum"], ["bus", "number"]),
            "id": req(["LoadID", "ID"], ["id"]),
            "mw": req(["LoadMW"], ["load", "mw"]),
        }

    def _monitored_row(self, scenario: PortfolioScenario) -> dict[str, Any] | None:
        rows = self.doctor.branch_snapshot()
        for row in rows:
            if (
                {int(row["from"]), int(row["to"])}
                == {scenario.monitored.from_bus, scenario.monitored.to_bus}
                and str(row["circuit"]) == str(scenario.monitored.circuit)
            ):
                return row
        return None

    def replay(
        self,
        scenario: PortfolioScenario,
        optimized: dict[str, Any],
    ) -> dict[str, Any]:
        lf = self._load_field_map()
        base_load_rows = self.adapter.get_rows("LOAD", [lf["bus"], lf["id"], lf["mw"]])
        base_load = {
            (int(r[lf["bus"]]), str(r[lf["id"]])): float(r[lf["mw"]])
            for r in base_load_rows
        }

        assets = {
            f"{a.bus}/{a.gen_id}": a
            for a in self.storage.rows(battery_only=True)
        }
        gen_map = {
            (g.bus, g.gen_id): g
            for g in self.gens.rows()
        }
        balancer = gen_map.get((scenario.balancing_bus, scenario.balancing_gen_id))
        if not balancer:
            raise RuntimeError("Scenario balancing generator not found.")

        baseline_n1 = NativeContingencyEngine(self.adapter).run_all()
        baseline_mon = self._monitored_row(scenario)
        hours = []

        self.adapter.save_state()
        try:
            for hour in optimized["schedule"]:
                # Begin every static timepoint from the same original case.
                self.adapter.save_state()
                try:
                    load_delta_total = 0.0
                    for (bus, lid), base_mw in base_load.items():
                        target = base_mw * float(hour["load_multiplier"])
                        load_delta_total += target - base_mw
                        self.adapter.change_single(
                            "LOAD",
                            [lf["bus"], lf["id"], lf["mw"]],
                            [bus, lid, target],
                        )

                    bess_delta_total = 0.0
                    requested = {}
                    for action in hour["actions"]:
                        key = f"{action['bus']}/{action['id']}"
                        asset = assets[key]
                        target = float(action["dispatch_mw"])
                        bess_delta_total += target - float(asset.mw)
                        requested[key] = target

                        control = gen_map[(asset.bus, asset.gen_id)]
                        self.gens.set_mw(control, target)

                    balancer_target = (
                        balancer.mw
                        + load_delta_total
                        - bess_delta_total
                    )
                    if (
                        balancer_target < balancer.min_mw - 1e-9
                        or balancer_target > balancer.max_mw + 1e-9
                    ):
                        raise RuntimeError(
                            f"Balancer target {balancer_target:.1f} MW exceeds "
                            f"{balancer.min_mw:.1f}..{balancer.max_mw:.1f} MW."
                        )
                    self.gens.set_mw(balancer, balancer_target)
                    self.adapter.run_script(
                        "EnterMode(PowerFlow); SolvePowerFlow(RECTNEWT);"
                    )

                    actual = {
                        key: self.gens.read_mw(a.bus, a.gen_id)
                        for key, a in assets.items()
                    }
                    held = all(
                        actual.get(key) is not None
                        and abs(float(actual[key]) - target) <= 1.0
                        for key, target in requested.items()
                    )

                    monitored = self._monitored_row(scenario)
                    n1 = NativeContingencyEngine(self.adapter).run_all()

                    hours.append({
                        "timestamp": hour["timestamp"],
                        "load_multiplier": hour["load_multiplier"],
                        "energy_price_per_mwh": hour["energy_price_per_mwh"],
                        "required_relief_mw": hour["required_relief_mw"],
                        "scheduled_portfolio_relief_mw": hour["portfolio_relief_mw"],
                        "scheduled_residual_relief_mw": hour["residual_relief_mw"],
                        "actions": hour["actions"],
                        "actual_bess_mw": actual,
                        "bess_setpoints_held": held,
                        "balancer_target_mw": balancer_target,
                        "monitored_branch": monitored,
                        "n1_violation_count": len(n1.violations),
                        "n1_unsolved_count": n1.unsolved_count,
                        "n1_processed_count": n1.processed_count,
                    })
                finally:
                    self.adapter.load_state()
        finally:
            self.adapter.load_state()

        peak_loading = max(
            (
                float(h["monitored_branch"]["loading_pct"])
                for h in hours
                if h.get("monitored_branch")
                and h["monitored_branch"].get("loading_pct") is not None
            ),
            default=None,
        )
        all_held = all(h["bess_setpoints_held"] for h in hours)
        max_n1_viol = max((h["n1_violation_count"] for h in hours), default=0)
        max_unsolved = max((h["n1_unsolved_count"] for h in hours), default=0)

        return {
            "scenario": scenario.to_dict(),
            "baseline_monitored_branch": baseline_mon,
            "baseline_n1_violation_count": len(baseline_n1.violations),
            "hours": hours,
            "summary": {
                "timepoints": len(hours),
                "all_bess_setpoints_held": all_held,
                "peak_monitored_loading_pct": peak_loading,
                "max_n1_violation_count": max_n1_viol,
                "max_n1_unsolved_count": max_unsolved,
            },
            "state_restored": True,
            "replay_method": "PROTECTED_PRODUCT_OWNED_STATIC_TIMEPOINTS",
            "guardrail": (
                "This replay is not the native PowerWorld TSS result table. "
                "Native TSS execution is available separately for preconfigured TSS cases."
            ),
        }
