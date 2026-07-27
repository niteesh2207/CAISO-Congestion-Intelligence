from __future__ import annotations

from dataclasses import dataclass
from typing import Any
import math

from ..field_catalog import FieldCatalog
from .model_doctor import ModelDoctor
from .object_resolver import BranchIdentity
from .sensitivity import SensitivityEngine


def _num(value: Any) -> float | None:
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def _same_branch(row: dict[str, Any], identity: BranchIdentity) -> bool:
    return (
        {int(row["from"]), int(row["to"])} == {identity.from_bus, identity.to_bus}
        and str(row["circuit"]).strip() == str(identity.circuit).strip()
    )


@dataclass
class EconomicSnapshot:
    buses: list[dict[str, Any]]
    binding_branches: list[dict[str, Any]]
    binding_interfaces: list[dict[str, Any]]
    warnings: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "buses": self.buses,
            "binding_branches": self.binding_branches,
            "binding_interfaces": self.binding_interfaces,
            "warnings": self.warnings,
        }


class ConstraintEconomics:
    """
    OPF economic interpretation layer.

    Exact price decomposition comes from PowerWorld bus marginal-price fields
    when those fields are available.

    Constraint marginal cost and signed PTDF are used only as a screening signal
    for likely price-spread drivers. They are NOT represented as an exact
    reconstruction of the bus congestion component, especially for MVA limits,
    losses, multiple simultaneous constraints, interfaces, or SCOPF constraints.
    """

    def __init__(self, adapter) -> None:
        self.adapter = adapter
        self.catalog = FieldCatalog(adapter)
        self.doctor = ModelDoctor(adapter)
        self.sensitivity = SensitivityEngine(adapter)

    def _find_bus_price_fields(self) -> dict[str, str | None]:
        total = (
            self.catalog.choose("BUS", ["BusMWMarginalCost", "MWMarginalCost"])
            or self.catalog.find_semantic("BUS", include=["mw", "marginal", "cost"])
        )

        energy = (
            self.catalog.choose(
                "BUS",
                ["BusMWMarginalCostEnergy", "MWMarginalCostEnergy", "LMPEnergy"],
            )
            or self.catalog.find_semantic("BUS", include=["energy", "marginal"])
            or self.catalog.find_semantic("BUS", include=["energy", "cost"])
        )
        congestion = (
            self.catalog.choose(
                "BUS",
                ["BusMWMarginalCostCongestion", "MWMarginalCostCongestion", "LMPCongestion"],
            )
            or self.catalog.find_semantic("BUS", include=["congestion", "marginal"])
            or self.catalog.find_semantic("BUS", include=["congestion", "cost"])
        )
        loss = (
            self.catalog.choose(
                "BUS",
                ["BusMWMarginalCostLoss", "BusMWMarginalCostLosses", "MWMarginalCostLoss", "LMPLoss"],
            )
            or self.catalog.find_semantic("BUS", include=["loss", "marginal"])
            or self.catalog.find_semantic("BUS", include=["loss", "cost"])
        )

        # Avoid accidentally reusing the total field as a component.
        for name, field in [("energy", energy), ("congestion", congestion), ("loss", loss)]:
            if field and total and field.lower() == total.lower():
                if name == "energy":
                    energy = None
                elif name == "congestion":
                    congestion = None
                else:
                    loss = None

        return {
            "total": total,
            "energy": energy,
            "congestion": congestion,
            "loss": loss,
        }

    def bus_prices(self) -> tuple[list[dict[str, Any]], list[str]]:
        fields = self._find_bus_price_fields()
        warnings: list[str] = []

        bus = self.catalog.choose("BUS", ["BusNum"])
        name = self.catalog.choose("BUS", ["BusName"])
        if not bus or not fields["total"]:
            return [], ["Could not resolve bus number and total MW marginal-cost fields."]

        requested = [bus, fields["total"]]
        if name:
            requested.append(name)
        for field in (fields["energy"], fields["congestion"], fields["loss"]):
            if field:
                requested.append(field)

        rows = self.adapter.get_rows("BUS", list(dict.fromkeys(requested)))
        result = []
        for row in rows:
            total = _num(row.get(fields["total"]))
            if total is None:
                continue
            item = {
                "bus": int(row[bus]),
                "name": str(row.get(name, "")) if name else "",
                "lmp_per_mwh": total,
                "energy_per_mwh": _num(row.get(fields["energy"])) if fields["energy"] else None,
                "congestion_per_mwh": _num(row.get(fields["congestion"])) if fields["congestion"] else None,
                "loss_per_mwh": _num(row.get(fields["loss"])) if fields["loss"] else None,
            }
            item["component_sum_per_mwh"] = (
                sum(
                    x for x in [
                        item["energy_per_mwh"],
                        item["congestion_per_mwh"],
                        item["loss_per_mwh"],
                    ]
                    if x is not None
                )
                if all(
                    item[k] is not None
                    for k in ["energy_per_mwh", "congestion_per_mwh", "loss_per_mwh"]
                )
                else None
            )
            result.append(item)

        missing = [
            label for label, field in [
                ("energy", fields["energy"]),
                ("congestion", fields["congestion"]),
                ("loss", fields["loss"]),
            ]
            if not field
        ]
        if missing:
            warnings.append(
                "Native bus LMP component fields were not resolved for: "
                + ", ".join(missing)
                + ". Total LMP remains usable, but exact component decomposition is incomplete."
            )
        return result, warnings

    def binding_branches(self) -> tuple[list[dict[str, Any]], list[str]]:
        warnings: list[str] = []
        f_from = self.catalog.choose("BRANCH", ["BusNum", "BusNumFrom"])
        f_to = self.catalog.choose("BRANCH", ["BusNum:1", "BusNumTo"])
        f_ckt = self.catalog.choose("BRANCH", ["LineCircuit", "Circuit"])
        if not all([f_from, f_to, f_ckt]):
            return [], ["Could not resolve branch identity fields for OPF economics."]

        constraint = (
            self.catalog.choose(
                "BRANCH",
                ["LineOPFConstraint", "OPFConstraint", "Constraint"],
            )
            or self.catalog.find_semantic("BRANCH", include=["constraint"])
        )
        marginal = (
            self.catalog.choose(
                "BRANCH",
                ["LineMVAMarginalCost", "MVAMarginalCost", "MVAMargCost"],
            )
            or self.catalog.find_semantic(
                "BRANCH", include=["mva", "marginal", "cost"]
            )
            or self.catalog.find_semantic(
                "BRANCH", include=["marginal", "cost"], exclude=["bus"]
            )
        )
        monitor = (
            self.catalog.choose("BRANCH", ["LineOPFMonitor", "OPFMonitor", "Monitor"])
            or self.catalog.find_semantic("BRANCH", include=["opf", "monitor"])
        )

        branch_state = self.doctor.branch_snapshot()
        state_map = {
            (
                min(int(r["from"]), int(r["to"])),
                max(int(r["from"]), int(r["to"])),
                str(r["circuit"]),
            ): r
            for r in branch_state
        }

        fields = [f_from, f_to, f_ckt] + [x for x in (constraint, marginal, monitor) if x]
        rows = self.adapter.get_rows("BRANCH", list(dict.fromkeys(fields)))
        result = []

        for row in rows:
            key = (
                min(int(row[f_from]), int(row[f_to])),
                max(int(row[f_from]), int(row[f_to])),
                str(row[f_ckt]),
            )
            state = state_map.get(key, {})
            status = str(row.get(constraint, "")) if constraint else ""
            mc = _num(row.get(marginal)) if marginal else None
            explicit_binding = "BIND" in status.upper()
            explicit_unenforceable = "UNENFORCE" in status.upper()
            economic_binding = mc is not None and abs(mc) > 1e-8

            if not (explicit_binding or explicit_unenforceable or economic_binding):
                continue

            result.append({
                "from": int(row[f_from]),
                "to": int(row[f_to]),
                "circuit": str(row[f_ckt]),
                "constraint_status": status or (
                    "Binding" if economic_binding else "Unknown"
                ),
                "monitor": row.get(monitor) if monitor else None,
                "marginal_cost_per_mva_hour": mc,
                "flow_mw": state.get("mw"),
                "flow_mva": state.get("mva"),
                "limit_mva": state.get("limit_mva"),
                "loading_pct": state.get("loading_pct"),
                "evidence_quality": (
                    "EXPLICIT_STATUS_AND_MARGINAL_COST"
                    if explicit_binding and economic_binding
                    else "EXPLICIT_STATUS"
                    if explicit_binding or explicit_unenforceable
                    else "NONZERO_MARGINAL_COST"
                ),
            })

        result.sort(
            key=lambda r: abs(r["marginal_cost_per_mva_hour"] or 0.0),
            reverse=True,
        )
        if not constraint:
            warnings.append(
                "A dedicated branch OPF constraint-status field was not resolved; "
                "nonzero marginal cost is being used as the binding signal."
            )
        if not marginal:
            warnings.append(
                "A branch MVA marginal-cost field was not resolved; binding economics are incomplete."
            )
        return result, warnings

    def binding_interfaces(self) -> tuple[list[dict[str, Any]], list[str]]:
        # Interface field naming varies substantially by case/version.
        # Dynamic discovery keeps this optional rather than guessing.
        try:
            fields = self.catalog.fields("INTERFACE")
        except Exception:
            return [], []

        if not fields:
            return [], []

        name = (
            self.catalog.choose("INTERFACE", ["InterfaceName", "Name", "Label"])
            or self.catalog.find_semantic("INTERFACE", include=["name"])
        )
        number = (
            self.catalog.choose("INTERFACE", ["InterfaceNum", "Number"])
            or self.catalog.find_semantic("INTERFACE", include=["number"])
        )
        status = (
            self.catalog.choose("INTERFACE", ["Constraint", "InterfaceConstraint"])
            or self.catalog.find_semantic("INTERFACE", include=["constraint"])
        )
        marginal = (
            self.catalog.choose(
                "INTERFACE",
                ["InterfaceMWMarginalCost", "MWMarginalCost", "LimitMarginalCost"],
            )
            or self.catalog.find_semantic("INTERFACE", include=["marginal", "cost"])
        )
        flow = (
            self.catalog.choose("INTERFACE", ["InterfaceMW", "MWFlow"])
            or self.catalog.find_semantic("INTERFACE", include=["mw", "flow"])
        )
        limit = (
            self.catalog.choose("INTERFACE", ["InterfaceMWLimit", "MWLimit", "Limit"])
            or self.catalog.find_semantic("INTERFACE", include=["mw", "limit"])
        )

        key_fields = [x for x in (number, name) if x]
        if not key_fields:
            return [], ["Interface fields exist but no interface identifier field was resolved."]

        requested = key_fields + [x for x in (status, marginal, flow, limit) if x]
        rows = self.adapter.get_rows("INTERFACE", list(dict.fromkeys(requested)))
        result = []
        for row in rows:
            mc = _num(row.get(marginal)) if marginal else None
            text = str(row.get(status, "")) if status else ""
            binding = "BIND" in text.upper() or (mc is not None and abs(mc) > 1e-8)
            if not binding and "UNENFORCE" not in text.upper():
                continue
            f = _num(row.get(flow)) if flow else None
            lim = _num(row.get(limit)) if limit else None
            result.append({
                "number": row.get(number) if number else None,
                "name": str(row.get(name, "")) if name else "",
                "constraint_status": text or ("Binding" if binding else "Unknown"),
                "marginal_cost_per_mw_hour": mc,
                "flow_mw": f,
                "limit_mw": lim,
                "loading_pct": (
                    100.0 * abs(f) / abs(lim)
                    if f is not None and lim not in (None, 0)
                    else None
                ),
            })
        result.sort(
            key=lambda r: abs(r["marginal_cost_per_mw_hour"] or 0.0),
            reverse=True,
        )
        return result, []

    def snapshot(self) -> EconomicSnapshot:
        buses, w1 = self.bus_prices()
        branches, w2 = self.binding_branches()
        interfaces, w3 = self.binding_interfaces()
        return EconomicSnapshot(
            buses=buses,
            binding_branches=branches,
            binding_interfaces=interfaces,
            warnings=[*w1, *w2, *w3],
        )

    def spread(
        self,
        snapshot: EconomicSnapshot,
        *,
        source_bus: int,
        sink_bus: int,
    ) -> dict[str, Any]:
        by_bus = {int(row["bus"]): row for row in snapshot.buses}
        source = by_bus.get(int(source_bus))
        sink = by_bus.get(int(sink_bus))
        if not source or not sink:
            raise RuntimeError(
                f"Could not resolve both bus prices for {source_bus} and {sink_bus}."
            )

        def diff(field: str) -> float | None:
            a = source.get(field)
            b = sink.get(field)
            if a is None or b is None:
                return None
            return float(b) - float(a)

        components = {
            "total_spread_per_mwh": diff("lmp_per_mwh"),
            "energy_spread_per_mwh": diff("energy_per_mwh"),
            "congestion_spread_per_mwh": diff("congestion_per_mwh"),
            "loss_spread_per_mwh": diff("loss_per_mwh"),
        }

        available_components = {
            key: value for key, value in components.items()
            if key != "total_spread_per_mwh" and value is not None
        }
        dominant = (
            max(available_components, key=lambda k: abs(available_components[k]))
            if available_components else None
        )

        # Constraint driver screening. This is deliberately not labeled exact
        # LMP contribution because branch marginal cost is a rating-relaxation
        # value and the branch constraint may be MVA, not signed MW.
        driver_screen = []
        try:
            ptdfs = self.sensitivity.ptdf(source_bus, sink_bus, "DC")
            for constraint in snapshot.binding_branches:
                ptdf = next(
                    (
                        r["ptdf_pct"] for r in ptdfs
                        if {
                            int(r["from"]), int(r["to"])
                        } == {
                            int(constraint["from"]), int(constraint["to"])
                        }
                        and str(r["circuit"]) == str(constraint["circuit"])
                    ),
                    None,
                )
                mc = constraint.get("marginal_cost_per_mva_hour")
                if ptdf is None or mc is None:
                    continue
                driver_screen.append({
                    "branch": (
                        f"{constraint['from']}-{constraint['to']} "
                        f"{constraint['circuit']}"
                    ),
                    "ptdf_source_to_sink_pct": float(ptdf),
                    "constraint_marginal_cost_per_mva_hour": float(mc),
                    "economic_exposure_abs_per_mwh_screen": (
                        abs(float(mc) * float(ptdf) / 100.0)
                    ),
                    "interpretation": "SCREENING_SIGNAL_NOT_EXACT_LMP_CONTRIBUTION",
                })
            driver_screen.sort(
                key=lambda r: r["economic_exposure_abs_per_mwh_screen"],
                reverse=True,
            )
        except Exception:
            pass

        total = components["total_spread_per_mwh"]
        if total is None:
            direction = "UNKNOWN"
        elif total > 1e-9:
            direction = "SINK_PREMIUM"
        elif total < -1e-9:
            direction = "SOURCE_PREMIUM"
        else:
            direction = "FLAT"

        return {
            "source": source,
            "sink": sink,
            **components,
            "dominant_component": dominant,
            "price_direction": direction,
            "binding_constraint_driver_screen": driver_screen[:10],
            "driver_screen_warning": (
                "PTDF × branch marginal-cost is used only to rank likely economic relevance. "
                "Use native bus Energy/Congestion/Loss components as the authoritative price decomposition."
            ),
        }

    def branch_constraint(
        self,
        snapshot: EconomicSnapshot,
        identity: BranchIdentity,
    ) -> dict[str, Any] | None:
        return next(
            (row for row in snapshot.binding_branches if _same_branch(row, identity)),
            None,
        )

    @staticmethod
    def trading_translation(spread: dict[str, Any]) -> dict[str, Any]:
        total = spread.get("total_spread_per_mwh")
        cong = spread.get("congestion_spread_per_mwh")
        loss = spread.get("loss_spread_per_mwh")
        energy = spread.get("energy_spread_per_mwh")

        if total is None:
            headline = "Price spread unavailable."
        elif total > 0:
            headline = (
                f"Sink bus carries a modeled premium of ${total:.2f}/MWh versus the source bus."
            )
        elif total < 0:
            headline = (
                f"Source bus carries a modeled premium of ${abs(total):.2f}/MWh versus the sink bus."
            )
        else:
            headline = "The modeled source/sink LMP spread is approximately flat."

        parts = []
        if cong is not None:
            parts.append(("CONGESTION", abs(cong), cong))
        if loss is not None:
            parts.append(("LOSSES", abs(loss), loss))
        if energy is not None:
            parts.append(("ENERGY", abs(energy), energy))
        parts.sort(key=lambda x: x[1], reverse=True)

        primary = parts[0][0] if parts else "UNKNOWN"
        if primary == "CONGESTION":
            market_read = (
                "The modeled spread is primarily congestion-driven. The most relevant next question is "
                "which binding constraint has both high enforcement cost and high source-to-sink transfer exposure."
            )
        elif primary == "LOSSES":
            market_read = (
                "The modeled spread is primarily loss-driven rather than a pure transmission-capacity separation."
            )
        elif primary == "ENERGY":
            market_read = (
                "The modeled spread is primarily associated with the energy reference component rather than local congestion."
            )
        else:
            market_read = (
                "The native component fields are incomplete, so the model should not assign a precise economic cause yet."
            )

        return {
            "headline": headline,
            "primary_modeled_driver": primary,
            "market_read": market_read,
            "guardrail": (
                "This is PowerWorld model economics. It becomes a market/trading signal only after the case, "
                "offers, topology, outages, ratings, losses and market rules are calibrated to the target ISO/RTO."
            ),
        }
