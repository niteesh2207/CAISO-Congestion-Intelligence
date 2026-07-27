from __future__ import annotations

from dataclasses import dataclass
from itertools import product
from typing import Any
import math

from .bess import BESSIntelligence
from .storage_inventory import StorageInventory, StorageAsset
from .time_series_scenario import PortfolioScenario


@dataclass(frozen=True)
class PortfolioAction:
    bus: int
    gen_id: str
    dispatch_mw: float  # positive discharge, negative charge
    relief_mw: float
    soc_start_pct: float
    soc_end_pct: float

    def to_dict(self) -> dict[str, Any]:
        return vars(self)


class StoragePortfolioOptimizer:
    """
    Discretized multi-hour optimizer for existing BA batteries.

    Objective components:
    - residual contingency-relief penalty;
    - battery-throughput penalty;
    - synthetic/user-provided energy price;
    - terminal minimum SOC penalty.

    This is a product-owned optimization layer. It does not claim to be a
    PowerWorld multi-period OPF. PowerWorld TSS is integrated separately.
    """

    def __init__(self, adapter) -> None:
        self.adapter = adapter
        self.inventory = StorageInventory(adapter)
        self.bess = BESSIntelligence(adapter)

    @staticmethod
    def _asset_key(asset: StorageAsset) -> str:
        return f"{asset.bus}/{asset.gen_id}"

    @staticmethod
    def _soc_after(asset: StorageAsset, soc_pct: float, dispatch_mw: float, hours: float) -> float:
        if asset.energy_mwh in (None, 0):
            raise RuntimeError(
                f"Battery {asset.bus}/{asset.gen_id} lacks verified energy capacity."
            )
        e = float(asset.energy_mwh)
        soc = float(soc_pct) / 100.0
        if dispatch_mw > 0:
            eta = float(asset.discharge_efficiency or 1.0)
            soc -= dispatch_mw * hours / max(eta, 1e-9) / e
        elif dispatch_mw < 0:
            eta = float(asset.charge_efficiency or 1.0)
            soc += (-dispatch_mw) * hours * eta / e
        return 100.0 * soc

    @staticmethod
    def _candidate_dispatches(asset: StorageAsset, step_mw: float) -> list[float]:
        lo = float(asset.min_mw)
        hi = float(asset.max_mw)
        if step_mw <= 0:
            raise ValueError("step_mw must be positive.")
        values = {0.0, lo, hi}
        k0 = math.ceil(lo / step_mw)
        k1 = math.floor(hi / step_mw)
        for k in range(k0, k1 + 1):
            values.add(round(k * step_mw, 9))
        return sorted(v for v in values if lo - 1e-9 <= v <= hi + 1e-9)

    def _relief_coefficients(
        self,
        scenario: PortfolioScenario,
        assets: list[StorageAsset],
    ) -> dict[str, dict[str, float]]:
        one_mw = self.bess.screen(
            battery_mw=1.0,
            monitored=scenario.monitored,
            outage=scenario.outage,
            reference_bus=scenario.reference_bus,
            top_n=100,
        )
        rows = {}
        for row in one_mw["discharge_best_relief"] + one_mw["discharge_worst"]:
            rows[int(row["bus"])] = row

        result = {}
        for asset in assets:
            row = rows.get(asset.bus)
            if not row:
                raise RuntimeError(
                    f"No OTDF/BESS sensitivity row for storage bus {asset.bus}."
                )
            result[self._asset_key(asset)] = {
                "discharge_relief_per_mw": float(row["discharge_relief_mw"]),
                "charge_relief_per_mw": float(row["charge_relief_mw"]),
                "otdf_pct": float(row["otdf_pct"]),
            }
        return result

    def optimize(self, scenario: PortfolioScenario) -> dict[str, Any]:
        assets = self.inventory.rows(battery_only=True)
        if not assets:
            raise RuntimeError("No BA batteries found.")
        for asset in assets:
            if not asset.metadata_verified or asset.energy_mwh is None or asset.soc_pct is None:
                raise RuntimeError(
                    f"Battery {asset.bus}/{asset.gen_id} lacks verified SOC/MWh metadata."
                )

        coeff = self._relief_coefficients(scenario, assets)
        asset_keys = [self._asset_key(a) for a in assets]
        dispatch_options = {
            self._asset_key(a): self._candidate_dispatches(a, scenario.action_step_mw)
            for a in assets
        }

        # State key = rounded SOC tuple in asset order.
        soc_resolution_pct = 1.0
        max_states_per_hour = 250
        initial_soc = tuple(round(float(a.soc_pct) / soc_resolution_pct) * soc_resolution_pct for a in assets)
        states: dict[tuple[float, ...], tuple[float, list[dict[str, Any]]]] = {
            initial_soc: (0.0, [])
        }

        for tp in scenario.timepoints:
            next_states: dict[
                tuple[float, ...],
                tuple[float, list[dict[str, Any]]],
            ] = {}

            for soc_state, (cost_so_far, path) in states.items():
                option_lists = [dispatch_options[k] for k in asset_keys]

                for actions in product(*option_lists):
                    next_soc = []
                    action_rows = []
                    relief = 0.0
                    energy_component = 0.0
                    throughput_component = 0.0
                    feasible = True

                    for idx, (asset, key, dispatch) in enumerate(zip(assets, asset_keys, actions)):
                        start_soc = float(soc_state[idx])
                        end_soc = self._soc_after(asset, start_soc, dispatch, tp.duration_hours)

                        if (
                            asset.soc_min_pct is not None
                            and end_soc < float(asset.soc_min_pct) - 1e-8
                        ):
                            feasible = False
                            break
                        if (
                            asset.soc_max_pct is not None
                            and end_soc > float(asset.soc_max_pct) + 1e-8
                        ):
                            feasible = False
                            break

                        c = coeff[key]
                        if dispatch >= 0:
                            action_relief = dispatch * c["discharge_relief_per_mw"]
                        else:
                            action_relief = (-dispatch) * c["charge_relief_per_mw"]

                        relief += action_relief
                        # Positive discharge earns modeled energy value; charging
                        # pays price (or earns value when price is negative).
                        energy_component += (
                            -dispatch
                            * tp.energy_price_per_mwh
                            * tp.duration_hours
                        )
                        throughput_component += (
                            abs(dispatch)
                            * tp.duration_hours
                            * scenario.throughput_cost_per_mwh
                        )

                        next_soc.append(round(end_soc, 2))
                        action_rows.append({
                            "bus": asset.bus,
                            "id": asset.gen_id,
                            "dispatch_mw": dispatch,
                            "mode": (
                                "DISCHARGE" if dispatch > 1e-9
                                else "CHARGE" if dispatch < -1e-9
                                else "IDLE"
                            ),
                            "soc_start_pct": start_soc,
                            "soc_end_pct": end_soc,
                            "relief_mw": action_relief,
                            "otdf_pct": c["otdf_pct"],
                        })

                    if not feasible:
                        continue

                    residual = max(0.0, tp.required_relief_mw - relief)
                    relief_penalty = (
                        residual
                        * scenario.unserved_relief_penalty_per_mwh
                        * tp.duration_hours
                    )
                    hourly_cost = (
                        energy_component
                        + throughput_component
                        + relief_penalty
                    )

                    key_state = tuple(round(float(x) / soc_resolution_pct) * soc_resolution_pct for x in next_soc)
                    candidate_cost = cost_so_far + hourly_cost
                    candidate_path = path + [{
                        "timestamp": tp.timestamp,
                        "duration_hours": tp.duration_hours,
                        "load_multiplier": tp.load_multiplier,
                        "energy_price_per_mwh": tp.energy_price_per_mwh,
                        "required_relief_mw": tp.required_relief_mw,
                        "portfolio_relief_mw": relief,
                        "residual_relief_mw": residual,
                        "energy_value_component": energy_component,
                        "throughput_cost_component": throughput_component,
                        "relief_penalty_component": relief_penalty,
                        "hourly_objective": hourly_cost,
                        "actions": action_rows,
                    }]

                    prior = next_states.get(key_state)
                    if prior is None or candidate_cost < prior[0]:
                        next_states[key_state] = (candidate_cost, candidate_path)

            if not next_states:
                raise RuntimeError(
                    f"No feasible storage portfolio state remains at {tp.timestamp}."
                )
            # Beam pruning keeps the development optimizer interactive while
            # preserving the lowest-cost representative for each discretized SOC state.
            if len(next_states) > max_states_per_hour:
                # Beam score retains economically good paths while strongly
                # protecting trajectories that remain near terminal SOC targets.
                def beam_score(item):
                    soc_key, (running_cost, _path) = item
                    reserve_shortfall = 0.0
                    for idx, asset_key in enumerate(asset_keys):
                        target = scenario.terminal_soc_target_pct.get(asset_key)
                        if target is not None:
                            reserve_shortfall += max(0.0, float(target) - float(soc_key[idx]))
                    return running_cost + 10000.0 * reserve_shortfall

                ranked = sorted(next_states.items(), key=beam_score)[:max_states_per_hour]
                states = dict(ranked)
            else:
                states = next_states

        best = None
        fallback = None
        for soc_state, (base_cost, path) in states.items():
            terminal_penalty = 0.0
            terminal_detail = []
            hard_target_ok = True
            final_action_map = {
                f"{row['bus']}/{row['id']}": row
                for row in path[-1]["actions"]
            } if path else {}
            for idx, (asset, key) in enumerate(zip(assets, asset_keys)):
                target = scenario.terminal_soc_target_pct.get(key)
                actual = float(
                    final_action_map.get(key, {}).get(
                        "soc_end_pct", soc_state[idx]
                    )
                )
                shortfall = max(0.0, float(target) - actual) if target is not None else 0.0
                if shortfall > 1e-9:
                    hard_target_ok = False
                penalty = shortfall * scenario.terminal_soc_penalty_per_pct
                terminal_penalty += penalty
                terminal_detail.append({
                    "asset": key,
                    "terminal_soc_pct": actual,
                    "target_min_soc_pct": target,
                    "shortfall_pct_points": shortfall,
                    "penalty": penalty,
                })

            total = base_cost + terminal_penalty
            candidate = (total, path, terminal_detail, terminal_penalty)
            if fallback is None or total < fallback[0]:
                fallback = candidate
            if hard_target_ok and (best is None or total < best[0]):
                best = candidate

        # Terminal SOC targets are hard constraints whenever at least one
        # discretized feasible trajectory satisfies them. Fallback is retained
        # only to avoid hiding infeasibility in a coarse user-defined grid.
        terminal_targets_enforced = best is not None
        if best is None:
            best = fallback
        assert best is not None
        total, path, terminal_detail, terminal_penalty = best

        energy_mwh = 0.0
        discharge_mwh = 0.0
        charge_mwh = 0.0
        total_relief_mwh = 0.0
        total_residual_mwh = 0.0
        for hour in path:
            dt = hour["duration_hours"]
            total_relief_mwh += hour["portfolio_relief_mw"] * dt
            total_residual_mwh += hour["residual_relief_mw"] * dt
            for action in hour["actions"]:
                p = action["dispatch_mw"]
                energy_mwh += abs(p) * dt
                if p > 0:
                    discharge_mwh += p * dt
                elif p < 0:
                    charge_mwh += (-p) * dt

        return {
            "scenario": scenario.to_dict(),
            "assets": [a.to_dict() for a in assets],
            "relief_coefficients": coeff,
            "schedule": path,
            "terminal_soc": terminal_detail,
            "terminal_soc_penalty": terminal_penalty,
            "terminal_targets_enforced": terminal_targets_enforced,
            "objective_value": total,
            "portfolio_metrics": {
                "throughput_mwh": energy_mwh,
                "discharge_mwh": discharge_mwh,
                "charge_mwh": charge_mwh,
                "total_relief_mwh": total_relief_mwh,
                "unserved_relief_mwh": total_residual_mwh,
            },
            "optimization_method": "DISCRETIZED_BEAM_DYNAMIC_PROGRAMMING",
            "soc_resolution_pct": soc_resolution_pct,
            "max_states_per_hour": max_states_per_hour,
            "action_step_mw": scenario.action_step_mw,
            "market_status": (
                "SYNTHETIC_DEMO_ECONOMICS"
                if scenario.provenance == "SYNTHETIC_DEMO_SCENARIO"
                else "USER_SCENARIO_ECONOMICS"
            ),
            "guardrails": [
                "NOT_POWERWORLD_MULTI_PERIOD_OPF",
                "EXISTING_BA_UNITS_ONLY",
                "VERIFIED_SOC_MWH_REQUIRED",
                "STATIC_OTDF_RELIEF_COEFFICIENTS_WITHIN_HORIZON",
                "HEURISTIC_BEAM_PRUNING_NOT_GLOBAL_CONTINUOUS_OPTIMUM",
                "TERMINAL_SOC_TARGETS_HARD_WHEN_DISCRETELY_FEASIBLE",
                "ENERGY_PRICE_INPUT_REQUIRES_PROVENANCE",
            ],
        }
