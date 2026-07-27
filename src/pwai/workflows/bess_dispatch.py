from __future__ import annotations

from typing import Any
import re

from .constraint_economics import ConstraintEconomics
from .generator_controls import GeneratorInventory, GeneratorControl
from .native_contingency import NativeContingencyEngine
from .object_resolver import BranchIdentity
from .optimization import OptimizationIntelligence
from .security_compare import compare_security
from .storage_inventory import StorageInventory, StorageAsset


class BESSDispatchStudy:
    def __init__(self, adapter) -> None:
        self.adapter = adapter
        self.storage = StorageInventory(adapter)
        self.gens = GeneratorInventory(adapter)

    def _select_balancer(
        self,
        *,
        action: str,
        mw: float,
        explicit_bus: int | None,
        explicit_id: str | None,
        bess: StorageAsset,
    ) -> GeneratorControl:
        candidates = [
            g for g in self.gens.rows()
            if not (g.bus == bess.bus and g.gen_id == bess.gen_id)
        ]

        if explicit_bus is not None:
            matches = [
                g for g in candidates
                if g.bus == int(explicit_bus)
                and g.gen_id == str(explicit_id or "1")
            ]
            if len(matches) != 1:
                raise RuntimeError(
                    f"Balancing generator {explicit_bus}/{explicit_id or '1'} was not found."
                )
            chosen = matches[0]
        else:
            # Prefer the largest available opposite-direction headroom.
            if action == "DISCHARGE":
                eligible = [g for g in candidates if g.down_headroom_mw >= mw]
                eligible.sort(key=lambda g: g.down_headroom_mw, reverse=True)
            else:
                eligible = [g for g in candidates if g.up_headroom_mw >= mw]
                eligible.sort(key=lambda g: g.up_headroom_mw, reverse=True)
            if not eligible:
                raise RuntimeError("No balancing generator has enough MW headroom.")
            chosen = eligible[0]

        required = (
            chosen.down_headroom_mw if action == "DISCHARGE"
            else chosen.up_headroom_mw
        )
        if required + 1e-9 < mw:
            raise RuntimeError(
                f"Balancing generator {chosen.bus}/{chosen.gen_id} has only "
                f"{required:.1f} MW of required-direction headroom."
            )
        return chosen

    @staticmethod
    def _branch_by_identity(rows: list[dict[str, Any]], branch: BranchIdentity | None):
        if branch is None:
            return None
        matches = [
            r for r in rows
            if {int(r["from"]), int(r["to"])} == {branch.from_bus, branch.to_bus}
            and str(r["circuit"]) == str(branch.circuit)
        ]
        return matches[0] if len(matches) == 1 else None

    def _economic_snapshot(
        self,
        *,
        solution_type: str,
        bess: StorageAsset,
        target_mw: float,
        source_bus: int | None,
        sink_bus: int | None,
    ) -> dict[str, Any] | None:
        optimizer = OptimizationIntelligence(self.adapter)
        preflight = optimizer.preflight(solution_type)
        if not preflight["capability_available"]:
            return None

        result = optimizer.run(solution_type)
        actual_bess = self.gens.read_mw(bess.bus, bess.gen_id)
        held = actual_bess is not None and abs(actual_bess - target_mw) <= 1.0

        econ = ConstraintEconomics(self.adapter)
        snapshot = econ.snapshot()
        spread = None
        if source_bus is not None and sink_bus is not None:
            try:
                spread = econ.spread(
                    snapshot,
                    source_bus=source_bus,
                    sink_bus=sink_bus,
                )
            except Exception:
                spread = None

        return {
            "solution_type": solution_type,
            "optimization": result.to_dict(),
            "actual_bess_mw_after_optimization": actual_bess,
            "requested_bess_target_mw": target_mw,
            "bess_setpoint_held": held,
            "economic_result_valid_for_fixed_bess_action": held,
            "economics": snapshot.to_dict(),
            "spread": spread,
        }

    def run(
        self,
        *,
        bus: int,
        gen_id: str,
        action: str,
        requested_mw: float,
        duration_hours: float,
        balancing_bus: int | None,
        balancing_gen_id: str | None,
        monitored: BranchIdentity | None,
        source_bus: int | None = None,
        sink_bus: int | None = None,
    ) -> dict[str, Any]:
        action = action.upper()
        if action not in {"CHARGE", "DISCHARGE"}:
            raise ValueError("action must be CHARGE or DISCHARGE")
        if requested_mw <= 0:
            raise ValueError("requested_mw must be positive.")

        bess = self.storage.find(bus, gen_id)
        feasibility = bess.feasible_action_mw(action, duration_hours)
        if requested_mw > float(feasibility["feasible_mw"]) + 1e-9:
            raise RuntimeError(
                f"Requested {requested_mw:.1f} MW {action.lower()} exceeds the "
                f"V0.11 feasible limit of {feasibility['feasible_mw']:.1f} MW "
                f"for {duration_hours:.2f} h."
            )

        balancer = self._select_balancer(
            action=action,
            mw=requested_mw,
            explicit_bus=balancing_bus,
            explicit_id=balancing_gen_id,
            bess=bess,
        )

        base_branches = self.gens.adapter and __import__(
            "pwai.workflows.model_doctor", fromlist=["ModelDoctor"]
        ).ModelDoctor(self.adapter).branch_snapshot()
        base_mon = self._branch_by_identity(base_branches, monitored)
        baseline_n1 = NativeContingencyEngine(self.adapter).run_all()

        # Baseline economics are solved from the untouched starting state.
        baseline_opf = None
        baseline_scopf = None
        self.adapter.save_state()
        try:
            baseline_opf = self._economic_snapshot(
                solution_type="OPF",
                bess=bess,
                target_mw=bess.mw,
                source_bus=source_bus,
                sink_bus=sink_bus,
            )
        finally:
            self.adapter.load_state()

        self.adapter.save_state()
        try:
            baseline_scopf = self._economic_snapshot(
                solution_type="SCOPF",
                bess=bess,
                target_mw=bess.mw,
                source_bus=source_bus,
                sink_bus=sink_bus,
            )
        except Exception:
            baseline_scopf = None
        finally:
            self.adapter.load_state()

        delta = requested_mw if action == "DISCHARGE" else -requested_mw
        bess_target = bess.mw + delta
        balance_delta = -delta
        balancer_target = balancer.mw + balance_delta

        projected_soc = bess.projected_soc_pct(
            action, requested_mw, duration_hours
        )

        self.adapter.save_state()
        try:
            # Modify the existing BA generator and one balancing generator only.
            bess_control = next(
                g for g in self.gens.rows()
                if g.bus == bess.bus and g.gen_id == bess.gen_id
            )
            self.gens.set_mw(bess_control, bess_target)
            self.gens.set_mw(balancer, balancer_target)
            self.adapter.run_script("EnterMode(PowerFlow); SolvePowerFlow(RECTNEWT);")

            actual_bess_pf = self.gens.read_mw(bess.bus, bess.gen_id)
            actual_balancer_pf = self.gens.read_mw(
                balancer.bus, balancer.gen_id
            )
            control_held_pf = (
                actual_bess_pf is not None
                and actual_balancer_pf is not None
                and abs(actual_bess_pf - bess_target) <= 1.0
                and abs(actual_balancer_pf - balancer_target) <= 1.0
            )

            from pwai.workflows.model_doctor import ModelDoctor
            post_branches = ModelDoctor(self.adapter).branch_snapshot()
            post_mon = self._branch_by_identity(post_branches, monitored)

            candidate_n1 = NativeContingencyEngine(self.adapter).run_all()
            security = compare_security(baseline_n1, candidate_n1).to_dict()

            # Candidate economics are each run from the exact same solved
            # physical BESS-action state. Nested state protection prevents OPF
            # redispatch from contaminating the following SCOPF study.
            candidate_opf = None
            candidate_scopf = None

            self.adapter.save_state()
            try:
                candidate_opf = self._economic_snapshot(
                    solution_type="OPF",
                    bess=bess,
                    target_mw=bess_target,
                    source_bus=source_bus,
                    sink_bus=sink_bus,
                )
            except Exception as exc:
                candidate_opf = {"error": str(exc)}
            finally:
                self.adapter.load_state()

            self.adapter.save_state()
            try:
                candidate_scopf = self._economic_snapshot(
                    solution_type="SCOPF",
                    bess=bess,
                    target_mw=bess_target,
                    source_bus=source_bus,
                    sink_bus=sink_bus,
                )
            except Exception as exc:
                candidate_scopf = {"error": str(exc)}
            finally:
                self.adapter.load_state()
        finally:
            self.adapter.load_state()

        def cost(snapshot):
            if not snapshot or "optimization" not in snapshot:
                return None
            return (
                snapshot["optimization"]
                .get("after", {})
                .get("total_generation_cost_per_hour")
            )

        def spread(snapshot):
            if not snapshot or not snapshot.get("spread"):
                return None
            return snapshot["spread"].get("total_spread_per_mwh")

        economics = {
            "opf": {
                "baseline_cost_per_hour": cost(baseline_opf),
                "candidate_cost_per_hour": cost(candidate_opf),
                "cost_delta_per_hour": (
                    cost(candidate_opf) - cost(baseline_opf)
                    if cost(candidate_opf) is not None and cost(baseline_opf) is not None
                    else None
                ),
                "baseline_spread_per_mwh": spread(baseline_opf),
                "candidate_spread_per_mwh": spread(candidate_opf),
                "spread_delta_per_mwh": (
                    spread(candidate_opf) - spread(baseline_opf)
                    if spread(candidate_opf) is not None and spread(baseline_opf) is not None
                    else None
                ),
                "candidate_setpoint_held": (
                    candidate_opf.get("bess_setpoint_held")
                    if candidate_opf and "error" not in candidate_opf else False
                ),
            },
            "scopf": {
                "baseline_cost_per_hour": cost(baseline_scopf),
                "candidate_cost_per_hour": cost(candidate_scopf),
                "cost_delta_per_hour": (
                    cost(candidate_scopf) - cost(baseline_scopf)
                    if cost(candidate_scopf) is not None and cost(baseline_scopf) is not None
                    else None
                ),
                "baseline_spread_per_mwh": spread(baseline_scopf),
                "candidate_spread_per_mwh": spread(candidate_scopf),
                "spread_delta_per_mwh": (
                    spread(candidate_scopf) - spread(baseline_scopf)
                    if spread(candidate_scopf) is not None and spread(baseline_scopf) is not None
                    else None
                ),
                "candidate_setpoint_held": (
                    candidate_scopf.get("bess_setpoint_held")
                    if candidate_scopf and "error" not in candidate_scopf else False
                ),
            },
        }

        return {
            "asset": bess.to_dict(),
            "action": action,
            "requested_mw": requested_mw,
            "duration_hours": duration_hours,
            "feasibility": feasibility,
            "projected_soc_pct": projected_soc,
            "bess_target_mw": bess_target,
            "balancing_generator": {
                "bus": balancer.bus,
                "id": balancer.gen_id,
                "base_mw": balancer.mw,
                "target_mw": balancer_target,
                "selection": (
                    "USER_SELECTED"
                    if balancing_bus is not None
                    else "AUTOMATIC_MAX_HEADROOM"
                ),
            },
            "power_flow_control_held": control_held_pf,
            "actual_bess_mw_after_power_flow": actual_bess_pf,
            "actual_balancer_mw_after_power_flow": actual_balancer_pf,
            "monitored_branch": (
                {
                    "from": monitored.from_bus,
                    "to": monitored.to_bus,
                    "circuit": monitored.circuit,
                    "base": base_mon,
                    "post_action": post_mon,
                    "loading_delta_pct_points": (
                        float(post_mon["loading_pct"]) - float(base_mon["loading_pct"])
                        if base_mon and post_mon
                        and base_mon.get("loading_pct") is not None
                        and post_mon.get("loading_pct") is not None
                        else None
                    ),
                }
                if monitored else None
            ),
            "n1": {
                "baseline": baseline_n1.to_dict(),
                "candidate": candidate_n1.to_dict(),
                "comparison": security,
            },
            "economics": economics,
            "economics_detail": {
                "baseline_opf": baseline_opf,
                "candidate_opf": candidate_opf,
                "baseline_scopf": baseline_scopf,
                "candidate_scopf": candidate_scopf,
            },
            "state_restored": True,
            "guardrails": [
                "EXISTING_BA_UNIT_ONLY",
                "BALANCED_MW_ACTION",
                "NO_SILENT_OPF_CONTROL_CHANGE",
                "ECONOMICS_VALID_ONLY_IF_BESS_SETPOINT_HELD",
                "SOC_METADATA_NOT_PERSISTED_AFTER_HYPOTHETICAL_STUDY",
            ],
        }
