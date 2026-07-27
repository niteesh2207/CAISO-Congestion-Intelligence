from __future__ import annotations

from typing import Any
import json

from ..field_catalog import FieldCatalog
from ..resource_utils import project_or_package_resource
from .generator_controls import GeneratorInventory
from .storage_inventory import StorageInventory


class ReserveIntelligence:
    """
    OPF-reserves integration layer.

    Real mode discovers reserve fields/results and add-on evidence.
    Demo mode runs an explicitly synthetic merit-order reserve allocation;
    this is not represented as PowerWorld OPF Reserves.
    """

    def __init__(self, adapter) -> None:
        self.adapter = adapter
        self.catalog = FieldCatalog(adapter)
        self.gens = GeneratorInventory(adapter)

    def _config(self) -> dict[str, Any]:
        p = project_or_package_resource("config", "reserve_scenario.json")
        return json.loads(p.read_text(encoding="utf-8"))

    def discover(self) -> dict[str, Any]:
        info = self.adapter.program_information()
        addons = " ".join(str(x) for x in info.get("addons", [])).lower()
        add_on_signal = "reserve" in addons

        matched = {}
        for obj in ["GEN", "LOAD", "AREA", "ZONE", "BUS"]:
            try:
                fields = self.catalog.fields(obj)
            except Exception:
                fields = []
            rows = [
                {"variable": f.variable, "description": f.description}
                for f in fields
                if any(
                    term in f"{f.variable} {f.description}".lower()
                    for term in [
                        "reserve", "spinning", "supplemental", "regulating",
                        "rmcp", "cleared", "reserve price",
                    ]
                )
            ]
            if rows:
                matched[obj] = rows[:120]

        return {
            "opf_reserves_addon_signal": add_on_signal,
            "matched_fields": matched,
            "powerworld_mode": self.adapter.solver_backed,
            "important_constraint": (
                "Current PowerWorld documentation states OPF Reserves is integrated "
                "with Time Step Simulation, but is not used simultaneously with SCOPF."
            ),
        }

    def demo_market(self) -> dict[str, Any]:
        cfg = self._config()
        if self.adapter.solver_backed:
            return {
                "mode": "POWERWORLD_RESERVE_DISCOVERY",
                "discovery": self.discover(),
                "warning": "No reserve market clearing is synthesized for a real case.",
            }

        gen_controls = {(g.bus, g.gen_id): g for g in self.gens.rows()}
        storage = {
            (a.bus, a.gen_id): a
            for a in StorageInventory(self.adapter).rows(battery_only=True)
        }

        offers = []
        for p in cfg["providers"]:
            key = (int(p["bus"]), str(p["id"]))
            available = float(p["max_reserve_mw"])
            if p["type"] == "GEN":
                control = gen_controls.get(key)
                if control is None:
                    continue
                available = min(available, float(control.up_headroom_mw))
            elif p["type"] == "BESS":
                asset = storage.get(key)
                if asset is None:
                    continue
                available = min(
                    available,
                    float(asset.discharge_power_headroom_mw),
                )
            offers.append({
                **p,
                "available_mw": max(0.0, available),
            })

        offers.sort(key=lambda x: float(x["bid_usd_per_mwh"]))
        remaining = float(cfg["requirement_mw"])
        cleared = []
        for offer in offers:
            mw = min(remaining, float(offer["available_mw"]))
            if mw <= 0:
                continue
            cleared.append({
                **offer,
                "cleared_mw": mw,
                "hourly_cost_usd": mw * float(offer["bid_usd_per_mwh"]),
            })
            remaining -= mw
            if remaining <= 1e-9:
                break

        rmcp = (
            float(cleared[-1]["bid_usd_per_mwh"])
            if remaining <= 1e-9 and cleared else None
        )
        battery_opportunity = []
        for key, asset in storage.items():
            cleared_mw = sum(
                float(x["cleared_mw"])
                for x in cleared
                if x["type"] == "BESS"
                and int(x["bus"]) == key[0]
                and str(x["id"]) == key[1]
            )
            battery_opportunity.append({
                "battery": f"{key[0]}/{key[1]}",
                "discharge_headroom_mw": float(asset.discharge_power_headroom_mw),
                "reserve_cleared_mw": cleared_mw,
                "remaining_uncommitted_discharge_headroom_mw": max(
                    0.0,
                    float(asset.discharge_power_headroom_mw) - cleared_mw,
                ),
                "interpretation": (
                    "Reserve commitment consumes power capability that cannot "
                    "simultaneously be assumed available for full energy/congestion dispatch."
                ),
            })

        return {
            "mode": "DEMO_SYNTHETIC_RESERVE_MARKET",
            "service": cfg["service"],
            "requirement_mw": cfg["requirement_mw"],
            "cleared_mw": sum(x["cleared_mw"] for x in cleared),
            "shortfall_mw": max(0.0, remaining),
            "rmcp_usd_per_mwh": rmcp,
            "cleared_providers": cleared,
            "battery_opportunity": battery_opportunity,
            "total_hourly_cost_usd": sum(x["hourly_cost_usd"] for x in cleared),
            "provenance": cfg["provenance"],
            "guardrail": (
                "This demo is a deterministic merit-order illustration, not "
                "PowerWorld OPF Reserves. Real co-optimization and RMCP must come "
                "from the licensed OPF Reserves solver."
            ),
        }
