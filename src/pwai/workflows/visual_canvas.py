from __future__ import annotations

import math
from typing import Any

from ..field_catalog import FieldCatalog
from .model_doctor import ModelDoctor, num
from .storage_inventory import StorageInventory


class VisualGridCanvas:
    """
    Solver-evidence-first grid canvas.

    Geography is used when exposed by the case. When it is unavailable, the
    product generates a deterministic study layout and labels that layout as
    derived rather than geographic.
    """

    def __init__(self, adapter) -> None:
        self.adapter = adapter
        self.catalog = FieldCatalog(adapter)
        self.doctor = ModelDoctor(adapter)

    def _choose_bus(self, candidates: list[str], semantic: list[str] | None = None):
        f = self.catalog.choose("BUS", candidates)
        if not f and semantic:
            f = self.catalog.find_semantic("BUS", include=semantic)
        return f

    def _bus_rows(self) -> list[dict[str, Any]]:
        f_num = self._choose_bus(["BusNum"], ["bus", "number"])
        f_name = self._choose_bus(["BusName"], ["bus", "name"])
        f_kv = self._choose_bus(["BusKV", "NomKV"], ["kv"])
        f_v = self._choose_bus(["BusPUVolt"], ["pu", "volt"])
        f_lat = self._choose_bus(
            ["Latitude", "BusLatitude", "SubLatitude"],
            ["latitude"],
        )
        f_lon = self._choose_bus(
            ["Longitude", "BusLongitude", "SubLongitude"],
            ["longitude"],
        )
        fields = [x for x in [f_num, f_name, f_kv, f_v, f_lat, f_lon] if x]
        raw = self.adapter.get_rows("BUS", list(dict.fromkeys(fields)))
        out = []
        for row in raw:
            out.append({
                "bus": int(row[f_num]),
                "name": str(row.get(f_name, "")) if f_name else "",
                "kv": num(row.get(f_kv)) if f_kv else None,
                "voltage_pu": num(row.get(f_v)) if f_v else None,
                "lat": num(row.get(f_lat)) if f_lat else None,
                "lon": num(row.get(f_lon)) if f_lon else None,
            })
        return out

    @staticmethod
    def _derived_layout(rows: list[dict[str, Any]]) -> dict[int, tuple[float, float]]:
        if not rows:
            return {}
        n = len(rows)
        cx, cy, radius = 500.0, 350.0, min(280.0, 110.0 + n * 8.0)
        result = {}
        for i, row in enumerate(sorted(rows, key=lambda x: x["bus"])):
            a = -math.pi / 2 + (2 * math.pi * i / n)
            result[row["bus"]] = (
                cx + radius * math.cos(a),
                cy + radius * math.sin(a),
            )
        return result

    def build(
        self,
        *,
        source_bus: int | None = None,
        sink_bus: int | None = None,
        focus_branch: tuple[int, int, str] | None = None,
    ) -> dict[str, Any]:
        buses = self._bus_rows()
        branches = self.doctor.branch_snapshot()

        geo_available = all(
            row["lat"] is not None and row["lon"] is not None for row in buses
        ) and bool(buses)

        if geo_available:
            lats = [row["lat"] for row in buses]
            lons = [row["lon"] for row in buses]
            lat_min, lat_max = min(lats), max(lats)
            lon_min, lon_max = min(lons), max(lons)
            dx = max(lon_max - lon_min, 1e-9)
            dy = max(lat_max - lat_min, 1e-9)
            layout = {
                row["bus"]: (
                    60.0 + 880.0 * (row["lon"] - lon_min) / dx,
                    60.0 + 580.0 * (lat_max - row["lat"]) / dy,
                )
                for row in buses
            }
            layout_mode = "CASE_GEOGRAPHY"
        elif not self.adapter.solver_backed and hasattr(self.adapter, "buses"):
            layout = {
                int(row["BusNum"]): (float(row.get("x", 0)), float(row.get("y", 0)))
                for row in self.adapter.buses
            }
            layout_mode = "SYNTHETIC_DEMO_COORDINATES"
        else:
            layout = self._derived_layout(buses)
            layout_mode = "DERIVED_STUDY_LAYOUT"

        gen_by_bus: dict[int, list[dict[str, Any]]] = {}
        try:
            f_num = self.catalog.choose("GEN", ["BusNum"])
            f_id = self.catalog.choose("GEN", ["GenID", "ID"])
            f_mw = self.catalog.choose("GEN", ["GenMW"])
            f_type = self.catalog.choose("GEN", ["GenUnitType", "UnitType"])
            fields = [x for x in [f_num, f_id, f_mw, f_type] if x]
            if f_num and f_id:
                for row in self.adapter.get_rows("GEN", fields):
                    gen_by_bus.setdefault(int(row[f_num]), []).append({
                        "id": str(row[f_id]),
                        "mw": num(row.get(f_mw)) if f_mw else None,
                        "unit_type": str(row.get(f_type, "")) if f_type else "",
                    })
        except Exception:
            pass

        load_by_bus: dict[int, float] = {}
        try:
            f_num = self.catalog.choose("LOAD", ["BusNum"])
            f_mw = self.catalog.choose("LOAD", ["LoadMW"])
            if f_num and f_mw:
                for row in self.adapter.get_rows("LOAD", [f_num, f_mw]):
                    load_by_bus[int(row[f_num])] = (
                        load_by_bus.get(int(row[f_num]), 0.0) + float(row[f_mw])
                    )
        except Exception:
            pass

        storage_buses = set()
        try:
            storage_buses = {
                a.bus for a in StorageInventory(self.adapter).rows(battery_only=True)
            }
        except Exception:
            pass

        nodes = []
        for row in buses:
            v = row["voltage_pu"]
            severity = (
                "CRITICAL" if v is not None and v < 0.90
                else "HIGH" if v is not None and v < 0.93
                else "WATCH" if v is not None and v < 0.95
                else "NORMAL"
            )
            role = (
                "SOURCE" if source_bus == row["bus"]
                else "SINK" if sink_bus == row["bus"]
                else "BUS"
            )
            x, y = layout.get(row["bus"], (0.0, 0.0))
            nodes.append({
                **row,
                "x": x,
                "y": y,
                "severity": severity,
                "role": role,
                "generation": gen_by_bus.get(row["bus"], []),
                "load_mw": load_by_bus.get(row["bus"], 0.0),
                "has_bess": row["bus"] in storage_buses,
            })

        edges = []
        for row in branches:
            loading = row["loading_pct"]
            severity = (
                "CRITICAL" if loading is not None and loading >= 100
                else "HIGH" if loading is not None and loading >= 95
                else "WATCH" if loading is not None and loading >= 90
                else "NORMAL"
            )
            is_focus = (
                focus_branch is not None
                and {int(row["from"]), int(row["to"])}
                == {int(focus_branch[0]), int(focus_branch[1])}
                and str(row["circuit"]) == str(focus_branch[2])
            )
            edges.append({
                **row,
                "severity": severity,
                "focus": is_focus,
                "direction": (
                    "FROM_TO" if (row["mw"] or 0) >= 0 else "TO_FROM"
                ),
            })

        return {
            "layout_mode": layout_mode,
            "nodes": nodes,
            "edges": edges,
            "legend": {
                "node": ["NORMAL", "WATCH", "HIGH", "CRITICAL", "SOURCE", "SINK"],
                "edge": ["NORMAL", "WATCH", "HIGH", "CRITICAL", "FOCUS"],
            },
            "evidence_policy": (
                "Electrical values are solver-backed when using SimAuto. "
                "Only layout may be derived when geographic coordinates are absent."
            ),
        }
