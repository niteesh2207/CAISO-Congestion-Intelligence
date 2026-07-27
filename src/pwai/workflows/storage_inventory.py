from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
import json

from ..field_catalog import FieldCatalog
from ..resource_utils import project_or_package_resource


STORAGE_UNIT_TYPES = {
    "BA": "BATTERY",
    "ES": "ENERGY_STORAGE_OTHER",
    "FW": "FLYWHEEL",
    "CP": "CONCENTRATED_SOLAR_STORAGE",
    "PS": "PUMPED_STORAGE",
    "CE": "COMPRESSED_AIR",
}


@dataclass(frozen=True)
class StorageAsset:
    bus: int
    gen_id: str
    unit_type: str
    storage_class: str
    status: str
    mw: float
    min_mw: float
    max_mw: float
    opf_mw_control: str | None
    energy_mwh: float | None
    soc_pct: float | None
    soc_min_pct: float | None
    soc_max_pct: float | None
    charge_efficiency: float | None
    discharge_efficiency: float | None
    metadata_verified: bool
    metadata_source: str | None

    @property
    def discharge_power_headroom_mw(self) -> float:
        return max(0.0, self.max_mw - self.mw)

    @property
    def charge_power_headroom_mw(self) -> float:
        # Moving generator MW downward is charging; negative GenMW is allowed
        # only when the case's Min MW permits it.
        return max(0.0, self.mw - self.min_mw)

    def energy_limited_power_mw(
        self,
        action: str,
        duration_hours: float,
    ) -> float | None:
        if duration_hours <= 0:
            raise ValueError("duration_hours must be positive.")
        if (
            not self.metadata_verified
            or self.energy_mwh is None
            or self.soc_pct is None
            or self.soc_min_pct is None
            or self.soc_max_pct is None
        ):
            return None

        e = float(self.energy_mwh)
        soc = float(self.soc_pct) / 100.0
        soc_min = float(self.soc_min_pct) / 100.0
        soc_max = float(self.soc_max_pct) / 100.0

        if action.upper() == "DISCHARGE":
            eta = float(self.discharge_efficiency or 1.0)
            grid_mwh_available = max(0.0, (soc - soc_min) * e * eta)
            return grid_mwh_available / duration_hours

        if action.upper() == "CHARGE":
            eta = float(self.charge_efficiency or 1.0)
            internal_room_mwh = max(0.0, (soc_max - soc) * e)
            grid_mwh_room = internal_room_mwh / max(eta, 1e-9)
            return grid_mwh_room / duration_hours

        raise ValueError("action must be CHARGE or DISCHARGE.")

    def feasible_action_mw(
        self,
        action: str,
        duration_hours: float,
    ) -> dict[str, Any]:
        action = action.upper()
        power_limit = (
            self.discharge_power_headroom_mw
            if action == "DISCHARGE"
            else self.charge_power_headroom_mw
        )
        energy_limit = self.energy_limited_power_mw(action, duration_hours)
        feasible = (
            min(power_limit, energy_limit)
            if energy_limit is not None
            else power_limit
        )
        return {
            "action": action,
            "duration_hours": duration_hours,
            "power_headroom_mw": power_limit,
            "energy_limited_power_mw": energy_limit,
            "feasible_mw": max(0.0, feasible),
            "energy_feasibility_verified": energy_limit is not None,
        }

    def projected_soc_pct(
        self,
        action: str,
        mw: float,
        duration_hours: float,
    ) -> float | None:
        if not self.metadata_verified or self.energy_mwh in (None, 0) or self.soc_pct is None:
            return None
        e = float(self.energy_mwh)
        soc = float(self.soc_pct) / 100.0
        action = action.upper()

        if action == "DISCHARGE":
            eta = float(self.discharge_efficiency or 1.0)
            internal_mwh = mw * duration_hours / max(eta, 1e-9)
            return 100.0 * (soc - internal_mwh / e)

        if action == "CHARGE":
            eta = float(self.charge_efficiency or 1.0)
            internal_mwh = mw * duration_hours * eta
            return 100.0 * (soc + internal_mwh / e)

        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "bus": self.bus,
            "id": self.gen_id,
            "unit_type": self.unit_type,
            "storage_class": self.storage_class,
            "status": self.status,
            "mw": self.mw,
            "min_mw": self.min_mw,
            "max_mw": self.max_mw,
            "opf_mw_control": self.opf_mw_control,
            "discharge_power_headroom_mw": self.discharge_power_headroom_mw,
            "charge_power_headroom_mw": self.charge_power_headroom_mw,
            "energy_mwh": self.energy_mwh,
            "soc_pct": self.soc_pct,
            "soc_min_pct": self.soc_min_pct,
            "soc_max_pct": self.soc_max_pct,
            "charge_efficiency": self.charge_efficiency,
            "discharge_efficiency": self.discharge_efficiency,
            "metadata_verified": self.metadata_verified,
            "metadata_source": self.metadata_source,
        }


class StorageInventory:
    def __init__(self, adapter, metadata_path: str | Path | None = None) -> None:
        self.adapter = adapter
        self.catalog = FieldCatalog(adapter)
        if metadata_path is None:
            metadata_path = project_or_package_resource("config", "storage_assets.json")
        self.metadata_path = Path(metadata_path)

    def _metadata(self) -> dict[tuple[int, str], dict[str, Any]]:
        if not self.metadata_path.exists():
            return {}
        try:
            data = json.loads(self.metadata_path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        result = {}
        for row in data.get("assets", []):
            try:
                result[(int(row["bus"]), str(row["id"]))] = row
            except Exception:
                continue
        return result

    def _choose(self, candidates: list[str], semantic: list[str] | None = None) -> str | None:
        value = self.catalog.choose("GEN", candidates)
        if not value and semantic:
            value = self.catalog.find_semantic("GEN", include=semantic)
        return value

    def rows(self, battery_only: bool = False) -> list[StorageAsset]:
        fields = {
            "bus": self._choose(["BusNum"], ["bus", "number"]),
            "id": self._choose(["GenID", "ID"], ["id"]),
            "unit_type": self._choose(["GenUnitType", "UnitType"], ["unit", "type"]),
            "status": self._choose(["GenStatus", "Status"], ["status"]),
            "mw": self._choose(["GenMW"], ["gen", "mw"]),
            "min": self._choose(["GenMWMin", "GenMinMW", "MinMW"], ["min", "mw"]),
            "max": self._choose(["GenMWMax", "GenMaxMW", "MaxMW"], ["max", "mw"]),
            "opf": (
                self._choose(["GenOPFMWControl", "OPFMWControl"], ["opf", "mw", "control"])
            ),
        }
        required = ["bus", "id", "unit_type", "mw", "min", "max"]
        if any(not fields[k] for k in required):
            raise RuntimeError(
                "Could not resolve generator Bus/ID/Unit Type/MW/Min/Max fields for storage inventory."
            )

        requested = [fields[k] for k in required]
        for key in ["status", "opf"]:
            if fields[key]:
                requested.append(fields[key])

        metadata = self._metadata()
        assets = []
        for row in self.adapter.get_rows("GEN", list(dict.fromkeys(requested))):
            unit_type = str(row.get(fields["unit_type"], "")).strip().upper()
            if unit_type not in STORAGE_UNIT_TYPES:
                continue
            if battery_only and unit_type != "BA":
                continue

            bus = int(row[fields["bus"]])
            gid = str(row[fields["id"]])
            md = metadata.get((bus, gid), {})
            assets.append(StorageAsset(
                bus=bus,
                gen_id=gid,
                unit_type=unit_type,
                storage_class=STORAGE_UNIT_TYPES[unit_type],
                status=str(row.get(fields["status"], "UNKNOWN")) if fields["status"] else "UNKNOWN",
                mw=float(row[fields["mw"]]),
                min_mw=float(row[fields["min"]]),
                max_mw=float(row[fields["max"]]),
                opf_mw_control=str(row.get(fields["opf"])) if fields["opf"] else None,
                energy_mwh=float(md["energy_mwh"]) if md.get("energy_mwh") is not None else None,
                soc_pct=float(md["soc_pct"]) if md.get("soc_pct") is not None else None,
                soc_min_pct=float(md["soc_min_pct"]) if md.get("soc_min_pct") is not None else None,
                soc_max_pct=float(md["soc_max_pct"]) if md.get("soc_max_pct") is not None else None,
                charge_efficiency=float(md["charge_efficiency"]) if md.get("charge_efficiency") is not None else None,
                discharge_efficiency=float(md["discharge_efficiency"]) if md.get("discharge_efficiency") is not None else None,
                metadata_verified=bool(md.get("verified", False)),
                metadata_source=md.get("source"),
            ))
        return assets

    def find(self, bus: int, gen_id: str) -> StorageAsset:
        matches = [
            row for row in self.rows(battery_only=True)
            if row.bus == int(bus) and row.gen_id == str(gen_id)
        ]
        if len(matches) != 1:
            raise RuntimeError(
                f"Expected one BA battery at bus {bus} id {gen_id}; found {len(matches)}."
            )
        return matches[0]
