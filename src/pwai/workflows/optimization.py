from __future__ import annotations

from dataclasses import dataclass
from typing import Any
import itertools
import math

from .capabilities import CapabilityRegistry
from .generator_controls import GeneratorInventory
from .model_doctor import ModelDoctor
from .native_contingency import NativeContingencyEngine
from .sensitivity import SensitivityEngine


@dataclass
class OptimizationResult:
    solution_type: str
    capability_snapshot: dict[str, Any]
    preflight: dict[str, Any]
    before: dict[str, Any]
    after: dict[str, Any]
    security_audit: dict[str, Any] | None
    warnings: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "solution_type": self.solution_type,
            "capability_snapshot": self.capability_snapshot,
            "preflight": self.preflight,
            "before": self.before,
            "after": self.after,
            "security_audit": self.security_audit,
            "warnings": self.warnings,
        }


class OptimizationIntelligence:
    def __init__(self, adapter) -> None:
        self.adapter = adapter
        self.capabilities = CapabilityRegistry(adapter)
        self.generators = GeneratorInventory(adapter)
        self.doctor = ModelDoctor(adapter)
        self.sensitivity = SensitivityEngine(adapter)

    def _choose(self, obj: str, candidates: list[str], include: list[str] | None = None) -> str | None:
        field = self.doctor.catalog.choose(obj, candidates)
        if not field and include:
            field = self.doctor.catalog.find_semantic(obj, include=include)
        return field

    def _generator_snapshot(self) -> list[dict[str, Any]]:
        cat = self.doctor.catalog
        bus = cat.choose("GEN", ["BusNum"])
        gid = cat.choose("GEN", ["GenID", "ID"])
        mw = cat.choose("GEN", ["GenMW"])
        if not all([bus, gid, mw]):
            return []

        cost = (
            cat.choose("GEN", ["GenCostPerHour", "Cost"])
            or cat.find_semantic("GEN", include=["cost", "hr"])
        )
        delta = (
            cat.choose("GEN", ["GenDeltaMW", "DeltaMW"])
            or cat.find_semantic("GEN", include=["delta", "mw"])
        )
        marginal = (
            cat.choose("GEN", ["GenMarginalCost", "ICForOPF", "IC"])
            or cat.find_semantic("GEN", include=["marginal", "cost"])
            or cat.find_semantic("GEN", include=["incremental", "cost"])
        )
        control = (
            cat.choose("GEN", ["GenOPFMWControl", "OPFMWControl"])
            or cat.find_semantic("GEN", include=["opf", "mw", "control"])
        )
        model = (
            cat.choose("GEN", ["GenCostModel", "CostModel"])
            or cat.find_semantic("GEN", include=["cost", "model"])
        )

        fields = [bus, gid, mw] + [x for x in (cost, delta, marginal, control, model) if x]
        rows = self.adapter.get_rows("GEN", list(dict.fromkeys(fields)))
        out = []
        for row in rows:
            item = {
                "bus": int(row[bus]),
                "id": str(row[gid]),
                "mw": float(row[mw]),
            }
            for label, field in [
                ("cost_per_hour", cost),
                ("delta_mw", delta),
                ("marginal_cost", marginal),
                ("opf_mw_control", control),
                ("cost_model", model),
            ]:
                if field:
                    value = row.get(field)
                    if label in {"cost_per_hour", "delta_mw", "marginal_cost"}:
                        try:
                            value = float(value)
                        except (TypeError, ValueError):
                            value = None
                    item[label] = value
            out.append(item)
        return out

    def _bus_lmps(self) -> list[dict[str, Any]]:
        cat = self.doctor.catalog
        bus = cat.choose("BUS", ["BusNum"])
        name = cat.choose("BUS", ["BusName"])
        lmp = (
            cat.choose("BUS", ["BusMWMarginalCost", "MWMarginalCost"])
            or cat.find_semantic("BUS", include=["mw", "marginal", "cost"])
        )
        if not bus or not lmp:
            return []

        fields = [bus, lmp] + ([name] if name else [])
        rows = self.adapter.get_rows("BUS", fields)
        result = []
        for row in rows:
            try:
                value = float(row[lmp])
            except (TypeError, ValueError):
                continue
            result.append({
                "bus": int(row[bus]),
                "name": str(row.get(name, "")) if name else "",
                "lmp_per_mwh": value,
            })
        return result

    @staticmethod
    def _sum_cost(gens: list[dict[str, Any]]) -> float | None:
        vals = [g.get("cost_per_hour") for g in gens]
        vals = [float(v) for v in vals if isinstance(v, (int, float))]
        return sum(vals) if vals else None

    def preflight(self, solution_type: str) -> dict[str, Any]:
        cap = self.capabilities.snapshot()
        required = "SCOPF" if solution_type.upper() == "SCOPF" else "OPF"
        available = bool(cap["capabilities"][required]["available"])

        gens = self._generator_snapshot()
        controllable = [
            g for g in gens
            if str(g.get("opf_mw_control", "")).strip().lower() not in {"no", "false", "0"}
        ]
        cost_models = [g for g in gens if g.get("cost_model") not in (None, "", "None")]

        warnings = []
        if not available:
            warnings.append(f"{required} add-on is not available according to ProgramInformation.")
        if not gens:
            warnings.append("No generator records could be read.")
        if gens and not controllable:
            warnings.append("No generator appears available for OPF MW control.")
        if gens and not cost_models:
            warnings.append(
                "No generator cost-model field was resolved. Minimum-cost interpretation may be incomplete."
            )

        return {
            "required_capability": required,
            "capability_available": available,
            "generator_count": len(gens),
            "apparently_controllable_generator_count": len(controllable),
            "generator_cost_model_count": len(cost_models),
            "warnings": warnings,
            "safe_to_attempt": available and bool(gens),
            "configuration_policy": (
                "USE_EXISTING_CASE_CONFIGURATION_ONLY: no automatic changes to OPF area status, "
                "generator OPF control, cost curves, monitored constraints, or contingency definitions."
            ),
        }

    def _capture(self) -> dict[str, Any]:
        gens = self._generator_snapshot()
        return {
            "generators": gens,
            "total_generation_cost_per_hour": self._sum_cost(gens),
            "bus_lmps": self._bus_lmps(),
            "branches": self.doctor.branch_snapshot(),
        }

    # ---------------- demo optimizer ----------------
    def _demo_candidate_flow(
        self,
        base_gens: dict[int, float],
        candidate_gens: dict[int, float],
    ) -> list[dict[str, Any]]:
        # Reuse the demo adapter's DC redispatch engine in protected state.
        controls = {g.bus: g for g in self.generators.rows()}
        self.adapter.save_state()
        try:
            for bus, new_mw in candidate_gens.items():
                control = controls[bus]
                self.generators.set_mw(control, new_mw)
            self.adapter.run_script("EnterMode(PowerFlow); SolvePowerFlow(RECTNEWT);")
            return self.doctor.branch_snapshot()
        finally:
            self.adapter.load_state()

    def _demo_dispatch(self, secure: bool) -> dict[str, Any]:
        controls = self.generators.rows()

        baseline_n1 = (
            NativeContingencyEngine(self.adapter).run_all()
            if secure else None
        )
        total = round(sum(g.mw for g in controls), 6)
        costs = {
            g.bus: next(
                float(row["GenMarginalCost"])
                for row in self.adapter.gens
                if int(row["BusNum"]) == g.bus and str(row["GenID"]) == g.gen_id
            )
            for g in controls
        }

        by_bus = {g.bus: g for g in controls}
        fixed_buses = {
            int(row["BusNum"])
            for row in self.adapter.gens
            if str(row.get("GenOPFMWControl", "")).strip().lower() in {"no", "false", "0"}
        }
        controllable_buses = sorted(bus for bus in by_bus if bus not in fixed_buses)
        fixed_dispatch = {bus: by_bus[bus].mw for bus in fixed_buses}
        controllable_total = total - sum(fixed_dispatch.values())

        if len(controllable_buses) < 1:
            raise RuntimeError("Demo OPF has no controllable generators.")

        step = 20.0
        candidates = []

        grids = []
        for bus in controllable_buses[:-1]:
            g = by_bus[bus]
            count = int((g.max_mw - g.min_mw) // step)
            values = [g.min_mw + i * step for i in range(count + 1)]
            if not values or values[-1] < g.max_mw - 1e-9:
                values.append(g.max_mw)
            grids.append(values)

        for values in itertools.product(*grids):
            dispatch = dict(fixed_dispatch)
            dispatch.update({
                bus: value
                for bus, value in zip(controllable_buses[:-1], values)
            })
            last = controllable_buses[-1]
            last_mw = controllable_total - sum(
                dispatch[bus] for bus in controllable_buses[:-1]
            )
            g_last = by_bus[last]
            if last_mw < g_last.min_mw - 1e-9 or last_mw > g_last.max_mw + 1e-9:
                continue
            dispatch[last] = last_mw

            branches = self._demo_candidate_flow(
                {g.bus: g.mw for g in controls},
                dispatch,
            )
            if any(
                row.get("loading_pct") is not None and float(row["loading_pct"]) > 100.0 + 1e-6
                for row in branches
            ):
                continue

            # For SCOPF demo, also run the demo N-1 engine under the candidate dispatch.
            n1_ok = True
            n1_summary = None
            if secure:
                self.adapter.save_state()
                try:
                    for bus, new_mw in dispatch.items():
                        self.generators.set_mw(by_bus[bus], new_mw)
                    self.adapter.run_script("EnterMode(PowerFlow); SolvePowerFlow(RECTNEWT);")
                    n1 = NativeContingencyEngine(self.adapter).run_all()
                    n1_summary = n1.to_dict()
                    n1_ok = n1.unsolved_count == 0 and len(n1.violations) == 0
                finally:
                    self.adapter.load_state()
            if not n1_ok:
                continue

            hourly_cost = sum(dispatch[bus] * costs[bus] for bus in dispatch)
            candidates.append((hourly_cost, dispatch, branches, n1_summary))

        if not candidates:
            raise RuntimeError(
                "The demo optimizer found no feasible dispatch under the configured constraints."
            )
        candidates.sort(key=lambda x: x[0])
        cost, dispatch, branches, n1_summary = candidates[0]

        # Apply winning dispatch to current protected optimization state.
        for bus, new_mw in dispatch.items():
            self.generators.set_mw(by_bus[bus], new_mw)
        self.adapter.run_script("EnterMode(PowerFlow); SolvePowerFlow(RECTNEWT);")

        # Populate synthetic OPF-result fields.
        for row in self.adapter.gens:
            bus = int(row["BusNum"])
            row["GenDeltaMW"] = dispatch[bus] - self.adapter._base_gen_mw[(bus, str(row["GenID"]))]
            row["GenCostPerHour"] = dispatch[bus] * costs[bus]

        self._demo_populate_economic_results(
            dispatch=dispatch,
            costs=costs,
        )

        # Synthetic PWLPOPFCTGViol-equivalent results for SCOPF explainability.
        # These records represent violations present before SCOPF and their
        # post-optimization status. Marginal costs are derived from the synthetic
        # security premium and total pre-SCOPF overload relief requirement.
        if secure:
            final_n1 = NativeContingencyEngine(self.adapter).run_all()
            final_by_sig = {
                (v.contingency, v.object_id, v.category): v
                for v in final_n1.violations
            }

            opf_cost_baseline = None
            # Use an independent protected OPF solve from the original demo state
            # only to estimate the synthetic security premium.
            self.adapter.save_state()
            try:
                # Restore base-generator values explicitly inside the temporary state.
                for g in self.generators.rows():
                    base = self.adapter._base_gen_mw[(g.bus, g.gen_id)]
                    self.generators.set_mw(g, base)
                self.adapter.run_script("EnterMode(PowerFlow); SolvePowerFlow(RECTNEWT);")
                controls2 = self.generators.rows()
                # Current demo OPF result is deterministic and available by repeating
                # the non-secure enumerated solve with fixed No-Control units held.
                total2 = round(sum(g.mw for g in controls2), 6)
                by_bus2 = {g.bus: g for g in controls2}
                fixed_buses2 = {
                    int(row["BusNum"])
                    for row in self.adapter.gens
                    if str(row.get("GenOPFMWControl", "")).strip().lower() in {"no", "false", "0"}
                }
                buses2 = sorted(bus for bus in by_bus2 if bus not in fixed_buses2)
                fixed_dispatch2 = {bus: by_bus2[bus].mw for bus in fixed_buses2}
                controllable_total2 = total2 - sum(fixed_dispatch2.values())
                costs2 = {
                    g.bus: next(
                        float(row["GenMarginalCost"])
                        for row in self.adapter.gens
                        if int(row["BusNum"]) == g.bus and str(row["GenID"]) == g.gen_id
                    )
                    for g in controls2
                }
                best = None
                grids2 = []
                for bus in buses2[:-1]:
                    g = by_bus2[bus]
                    step2 = 20.0
                    count = int((g.max_mw - g.min_mw) // step2)
                    values2 = [g.min_mw + i * step2 for i in range(count + 1)]
                    if not values2 or values2[-1] < g.max_mw - 1e-9:
                        values2.append(g.max_mw)
                    grids2.append(values2)
                for values2 in itertools.product(*grids2):
                    d2 = dict(fixed_dispatch2)
                    d2.update({bus: val for bus, val in zip(buses2[:-1], values2)})
                    last2 = buses2[-1]
                    lastmw2 = controllable_total2 - sum(values2)
                    glast2 = by_bus2[last2]
                    if lastmw2 < glast2.min_mw - 1e-9 or lastmw2 > glast2.max_mw + 1e-9:
                        continue
                    d2[last2] = lastmw2
                    br2 = self._demo_candidate_flow(
                        {g.bus: g.mw for g in controls2},
                        d2,
                    )
                    if any(
                        row.get("loading_pct") is not None
                        and float(row["loading_pct"]) > 100.0 + 1e-6
                        for row in br2
                    ):
                        continue
                    c2 = sum(d2[b] * costs2[b] for b in d2)
                    if best is None or c2 < best:
                        best = c2
                opf_cost_baseline = best
            finally:
                self.adapter.load_state()

            security_premium = max(
                0.0,
                float(cost) - float(opf_cost_baseline or cost),
            )

            base_violations = list(baseline_n1.violations) if baseline_n1 else []
            total_excess_mva = sum(
                max(0.0, float(v.value or 0.0) - float(v.limit or 0.0))
                for v in base_violations
            )
            marginal_per_mva = (
                security_premium / total_excess_mva
                if total_excess_mva > 1e-9 else 0.0
            )

            records = []
            for v in base_violations:
                sig = (v.contingency, v.object_id, v.category)
                final_v = final_by_sig.get(sig)
                new_pct = float(final_v.percent) if final_v and final_v.percent is not None else 100.0
                records.append({
                    "CTGName": v.contingency,
                    "Category": v.category,
                    "Element": f"BRANCH {v.object_id.replace('-', ' ', 1)}",
                    "Value": float(v.percent) if v.percent is not None else None,
                    "ScaledLimit": 100.0,
                    "NewValue": min(new_pct, 100.0),
                    "Error": min(new_pct, 100.0) - 100.0,
                    "Included": "Yes",
                    "MarginalCost": marginal_per_mva,
                    "Unenforceable": "No",
                    "SkipViolation": "No",
                    "DemoDerivation": "SECURITY_PREMIUM_DIVIDED_BY_TOTAL_INITIAL_EXCESS_MVA",
                })
            self.adapter.scopf_ctg_violations = records
        else:
            self.adapter.scopf_ctg_violations = []

        return {
            "objective_cost_per_hour": cost,
            "dispatch": dispatch,
            "n1_summary": n1_summary,
        }

    def _demo_populate_economic_results(
        self,
        *,
        dispatch: dict[int, float],
        costs: dict[int, float],
    ) -> None:
        """
        Construct a transparent lossless-DC economic decomposition for the demo.

        Generator buses that remain inside their MW limits anchor their nodal
        marginal prices to their synthetic marginal generation costs. The most
        heavily loaded lines are used as candidate active constraints and a
        small linear dual system is solved so those generator nodal prices are
        reproduced by:

            LMP_bus = Energy + Σ(mu_k * PTDF_bus,k)

        Loss component is zero because the demo sensitivity network is lossless.

        This is synthetic development economics, not PowerWorld output.
        """
        import numpy as np

        controls = {g.bus: g for g in self.generators.rows()}
        controllable_buses = {
            int(row["BusNum"])
            for row in self.adapter.gens
            if str(row.get("GenOPFMWControl", "")).strip().lower() not in {"no", "false", "0"}
        }
        interior = [
            bus for bus, mw in dispatch.items()
            if bus in controllable_buses
            and mw > controls[bus].min_mw + 1e-6
            and mw < controls[bus].max_mw - 1e-6
        ]
        if len(interior) < 2:
            return

        # Unconstrained energy marginal price: cost of the marginal generator
        # in an economic dispatch ignoring network constraints.
        total = sum(dispatch.values())
        remaining = total
        energy_price = None
        for bus in sorted(costs, key=lambda b: costs[b]):
            g = controls[bus]
            take = min(g.max_mw, max(g.min_mw, remaining))
            remaining -= take
            energy_price = costs[bus]
            if remaining <= 1e-9:
                break
        if energy_price is None:
            energy_price = costs[interior[0]]

        ref = min(interior, key=lambda bus: abs(costs[bus] - energy_price))
        others = [bus for bus in interior if bus != ref]

        branch_rows = self.doctor.branch_snapshot()
        needed = min(len(others), len(branch_rows))
        candidates = sorted(
            branch_rows,
            key=lambda r: float(r.get("loading_pct") or 0.0),
            reverse=True,
        )[:needed]
        if not candidates:
            return

        A = []
        b = []
        for bus in others[:needed]:
            rows = self.sensitivity.ptdf(bus, ref, "DC")
            A.append([
                (
                    next(
                        r["ptdf_pct"] for r in rows
                        if {
                            int(r["from"]), int(r["to"])
                        } == {int(c["from"]), int(c["to"])}
                        and str(r["circuit"]) == str(c["circuit"])
                    )
                    / 100.0
                )
                for c in candidates
            ])
            b.append(costs[bus] - costs[ref])

        A = np.asarray(A, dtype=float)
        b = np.asarray(b, dtype=float)
        if A.size == 0:
            return
        mu, *_ = np.linalg.lstsq(A, b, rcond=None)

        # Clear prior demo economic fields.
        for branch in self.adapter.branches:
            branch["LineOPFConstraint"] = ""
            branch["LineMVAMarginalCost"] = 0.0

        for constraint, dual in zip(candidates, mu):
            target = next(
                br for br in self.adapter.branches
                if {
                    int(br["BusNum"]), int(br["BusNum:1"])
                } == {int(constraint["from"]), int(constraint["to"])}
                and str(br["LineCircuit"]) == str(constraint["circuit"])
            )
            target["LineOPFConstraint"] = "Binding"
            target["LineMVAMarginalCost"] = abs(float(dual))

        # Native-style bus components.
        for bus_row in self.adapter.buses:
            bus = int(bus_row["BusNum"])
            if bus == ref:
                p = np.zeros(len(candidates), dtype=float)
            else:
                rows = self.sensitivity.ptdf(bus, ref, "DC")
                p = np.asarray([
                    (
                        next(
                            r["ptdf_pct"] for r in rows
                            if {
                                int(r["from"]), int(r["to"])
                            } == {int(c["from"]), int(c["to"])}
                            and str(r["circuit"]) == str(c["circuit"])
                        )
                        / 100.0
                    )
                    for c in candidates
                ], dtype=float)

            total_lmp = float(costs[ref] + p @ mu)
            bus_row["BusMWMarginalCost"] = total_lmp
            bus_row["BusMWMarginalCostEnergy"] = float(energy_price)
            bus_row["BusMWMarginalCostCongestion"] = total_lmp - float(energy_price)
            bus_row["BusMWMarginalCostLoss"] = 0.0

    def run(self, solution_type: str) -> OptimizationResult:
        kind = solution_type.upper()
        if kind not in {"OPF", "SCOPF"}:
            raise ValueError("solution_type must be OPF or SCOPF")

        capability_snapshot = self.capabilities.require(kind)
        preflight = self.preflight(kind)
        if not preflight["safe_to_attempt"]:
            raise RuntimeError("; ".join(preflight["warnings"]) or "Optimization preflight failed.")

        before = self._capture()
        warnings = list(preflight["warnings"])

        if self.adapter.solver_backed:
            if kind == "OPF":
                self.adapter.run_script("InitializePrimalLP;")
                self.adapter.run_script("SolvePrimalLP;")
            else:
                self.adapter.run_script("SolveFullSCOPF(OPF);")
        else:
            self._demo_dispatch(secure=(kind == "SCOPF"))

        after = self._capture()

        security_audit = None
        if kind == "SCOPF":
            audit = NativeContingencyEngine(self.adapter).run_all()
            security_audit = audit.to_dict()
            if audit.unsolved_count:
                warnings.append(
                    f"Post-SCOPF audit contains {audit.unsolved_count} unsolved contingencies."
                )
            if audit.violations:
                warnings.append(
                    f"Post-SCOPF audit still contains {len(audit.violations)} recorded contingency violations."
                )

        return OptimizationResult(
            solution_type=kind,
            capability_snapshot=capability_snapshot,
            preflight=preflight,
            before=before,
            after=after,
            security_audit=security_audit,
            warnings=warnings,
        )
