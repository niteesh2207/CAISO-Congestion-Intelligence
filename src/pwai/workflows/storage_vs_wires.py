from __future__ import annotations

from pathlib import Path
from typing import Any

from .beneficiary_mapping import map_lmp_beneficiaries
from .investment_math import annualized_cost, load_investment_assumptions
from .object_resolver import BranchIdentity
from .storage_portfolio import StoragePortfolioOptimizer
from .time_series_scenario import load_scenario
from .transmission_upgrade import TransmissionUpgradeStudy


class StorageVsWiresDecision:
    """
    Screening-level comparison of:
    - using the existing storage portfolio; versus
    - increasing the rating of one monitored transmission branch.

    Capital assumptions are explicit inputs. Nothing in this class invents
    construction cost, project life, or market-calibrated congestion hours.
    """

    def __init__(self, adapter) -> None:
        self.adapter = adapter

    def run(
        self,
        *,
        branch: BranchIdentity,
        source_bus: int | None = None,
        sink_bus: int | None = None,
    ) -> dict[str, Any]:
        assumptions = load_investment_assumptions()
        scenario = load_scenario()

        storage = StoragePortfolioOptimizer(self.adapter).optimize(scenario)

        # Zero-storage objective for the same scenario.
        zero_objective = sum(
            tp.required_relief_mw
            * scenario.unserved_relief_penalty_per_mwh
            * tp.duration_hours
            for tp in scenario.timepoints
        )
        storage_operational_value = max(
            0.0, zero_objective - float(storage["objective_value"])
        )

        tx = assumptions["transmission"]
        wire = TransmissionUpgradeStudy(self.adapter).run(
            branch=branch,
            delta_mva=float(tx["upgrade_delta_mva"]),
            source_bus=source_bus,
            sink_bus=sink_bus,
        )

        annual_hours = float(
            assumptions["analysis"]["representative_congested_hours_per_year"]
        )
        wire_opf_delta = wire["opf"]["cost_delta_per_hour"]
        wire_operational_value_annual = (
            max(0.0, -float(wire_opf_delta)) * annual_hours
            if wire_opf_delta is not None else None
        )

        wire_annualized = annualized_cost(
            float(tx["capex_usd"]),
            int(tx["economic_life_years"]),
            float(tx["discount_rate"]),
            float(tx["fixed_om_pct_capex_per_year"]),
        )

        st = assumptions["storage"]
        storage_annualized = annualized_cost(
            float(st["incremental_portfolio_capex_usd"]),
            int(st["economic_life_years"]),
            float(st["discount_rate"]),
            float(st["fixed_om_pct_capex_per_year"]),
        )

        baseline_buses = (
            wire.get("baseline_economics", {})
            .get("snapshot", {})
            .get("buses", [])
        )
        candidate_buses = (
            wire.get("candidate_economics", {})
            .get("snapshot", {})
            .get("buses", [])
        )
        beneficiaries = map_lmp_beneficiaries(
            baseline_buses,
            candidate_buses,
        )

        if wire_operational_value_annual is None:
            wire_net_screen = None
        else:
            wire_net_screen = wire_operational_value_annual - wire_annualized

        # Existing-storage capex is zero by default because this comparison asks
        # whether already-installed storage can defer/avoid a wire solution.
        storage_net_horizon = storage_operational_value - storage_annualized

        recommendation = "INSUFFICIENT_EVIDENCE"
        if wire_net_screen is not None:
            if storage["portfolio_metrics"]["unserved_relief_mwh"] <= 1e-6:
                recommendation = (
                    "EXISTING_STORAGE_CAN_MEET_CONFIGURED_HORIZON_RELIEF"
                    if storage_net_horizon >= wire_net_screen
                    else "WIRE_UPGRADE_HAS_STRONGER_SCREENED_ECONOMICS"
                )
            else:
                recommendation = (
                    "WIRE_OR_HYBRID_NEEDED_FOR_FULL_CONFIGURED_RELIEF"
                )

        return {
            "storage": {
                "portfolio": storage,
                "zero_storage_objective": zero_objective,
                "screened_horizon_operational_value": storage_operational_value,
                "annualized_incremental_fixed_cost": storage_annualized,
                "screened_net_value": storage_net_horizon,
            },
            "transmission": {
                "study": wire,
                "annualized_fixed_cost": wire_annualized,
                "screened_annual_operational_value": wire_operational_value_annual,
                "screened_net_annual_value": wire_net_screen,
                "assumed_congested_hours_per_year": annual_hours,
            },
            "beneficiary_mapping": beneficiaries,
            "recommendation": recommendation,
            "assumptions": assumptions,
            "guardrails": [
                "SCREENING_NOT_PROJECT_FINANCE",
                "NO_CONSTRUCTION_FEASIBILITY",
                "NO_MARKET_REVENUE_STACK",
                "CAPEX_AND_CONGESTED_HOURS_REQUIRE_USER_VALIDATION",
                "STORAGE_EXISTING_ASSET_ASSUMPTION",
            ],
        }
