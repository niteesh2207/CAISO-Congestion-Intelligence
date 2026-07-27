from __future__ import annotations

from typing import Any

from .constraint_economics import ConstraintEconomics
from .economic_parser import LMPSpreadRequest
from .market_calibration import MarketCalibrationAuditor
from .optimization import OptimizationIntelligence
from .scopf_economics import SCOPFContingencyEconomics


class SecurityPriceAttribution:
    """
    Compares OPF and SCOPF economics from the same original operating state.

    Exact model security effect:
      SCOPF bus LMP - OPF bus LMP
      SCOPF source/sink spread - OPF source/sink spread

    Contingency marginal cost × OTDF is retained only as a driver-screen ranking.
    """

    def __init__(self, adapter) -> None:
        self.adapter = adapter

    @staticmethod
    def _bus_map(snapshot) -> dict[int, dict[str, Any]]:
        return {int(row["bus"]): row for row in snapshot.buses}

    def run(
        self,
        *,
        spread_request: LMPSpreadRequest | None = None,
    ) -> dict[str, Any]:
        optimizer = OptimizationIntelligence(self.adapter)

        # OPF baseline from original state.
        self.adapter.save_state()
        try:
            opf_result = optimizer.run("OPF")
            opf_econ = ConstraintEconomics(self.adapter)
            opf_snapshot = opf_econ.snapshot()
            opf_spread = (
                opf_econ.spread(
                    opf_snapshot,
                    source_bus=spread_request.source_bus,
                    sink_bus=spread_request.sink_bus,
                )
                if spread_request else None
            )
        finally:
            self.adapter.load_state()

        # SCOPF from the same original state.
        self.adapter.save_state()
        try:
            scopf_result = optimizer.run("SCOPF")
            scopf_econ = ConstraintEconomics(self.adapter)
            scopf_snapshot = scopf_econ.snapshot()
            scopf_spread = (
                scopf_econ.spread(
                    scopf_snapshot,
                    source_bus=spread_request.source_bus,
                    sink_bus=spread_request.sink_bus,
                )
                if spread_request else None
            )

            ctg_engine = SCOPFContingencyEconomics(self.adapter)
            ctg_rows, ctg_warnings = ctg_engine.rows()
            ranked_ctgs = ctg_engine.rank(ctg_rows)
            exposure = (
                ctg_engine.source_sink_exposure(
                    ctg_rows,
                    source_bus=spread_request.source_bus,
                    sink_bus=spread_request.sink_bus,
                )
                if spread_request else []
            )
        finally:
            self.adapter.load_state()

        opf_bus = self._bus_map(opf_snapshot)
        scopf_bus = self._bus_map(scopf_snapshot)
        bus_security_delta = []
        for bus in sorted(set(opf_bus) & set(scopf_bus)):
            o = opf_bus[bus]
            s = scopf_bus[bus]
            bus_security_delta.append({
                "bus": bus,
                "opf_lmp_per_mwh": o.get("lmp_per_mwh"),
                "scopf_lmp_per_mwh": s.get("lmp_per_mwh"),
                "security_increment_per_mwh": (
                    float(s["lmp_per_mwh"]) - float(o["lmp_per_mwh"])
                    if s.get("lmp_per_mwh") is not None and o.get("lmp_per_mwh") is not None
                    else None
                ),
            })

        spread_delta = None
        if opf_spread and scopf_spread:
            total_opf = opf_spread.get("total_spread_per_mwh")
            total_scopf = scopf_spread.get("total_spread_per_mwh")
            spread_delta = {
                "opf_spread_per_mwh": total_opf,
                "scopf_spread_per_mwh": total_scopf,
                "security_incremental_spread_per_mwh": (
                    float(total_scopf) - float(total_opf)
                    if total_opf is not None and total_scopf is not None else None
                ),
                "opf_congestion_spread_per_mwh": opf_spread.get("congestion_spread_per_mwh"),
                "scopf_congestion_spread_per_mwh": scopf_spread.get("congestion_spread_per_mwh"),
            }

        calibration = MarketCalibrationAuditor().audit()

        return {
            "opf": {
                "optimization": opf_result.to_dict(),
                "economics": opf_snapshot.to_dict(),
                "spread": opf_spread,
            },
            "scopf": {
                "optimization": scopf_result.to_dict(),
                "economics": scopf_snapshot.to_dict(),
                "spread": scopf_spread,
                "contingency_constraints": ranked_ctgs,
                "contingency_constraint_warnings": ctg_warnings,
            },
            "bus_security_delta": bus_security_delta,
            "spread_security_delta": spread_delta,
            "contingency_driver_screen": exposure,
            "market_calibration": calibration,
            "guardrails": [
                "SCOPF LMP minus OPF LMP is an exact comparison of these two PowerWorld model solutions, not an ISO settlement decomposition.",
                "Contingency marginal cost × OTDF is only a driver-screen ranking.",
                "Market-trading interpretation remains MODEL_ECONOMICS until the market calibration gate passes.",
            ],
            "state_restored": True,
        }
