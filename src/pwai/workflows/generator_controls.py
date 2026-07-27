from __future__ import annotations
from dataclasses import dataclass
from typing import Any

from ..field_catalog import FieldCatalog


@dataclass(frozen=True)
class GeneratorControl:
    bus: int
    gen_id: str
    mw: float
    min_mw: float
    max_mw: float
    status: str | None

    @property
    def up_headroom_mw(self) -> float:
        return max(0.0, self.max_mw - self.mw)

    @property
    def down_headroom_mw(self) -> float:
        return max(0.0, self.mw - self.min_mw)


class GeneratorInventory:
    def __init__(self, adapter) -> None:
        self.adapter = adapter
        self.catalog = FieldCatalog(adapter)

    def _required(self, candidates: list[str], semantic: list[str] | None = None) -> str:
        field = self.catalog.choose("GEN", candidates)
        if field:
            return field
        if semantic:
            field = self.catalog.find_semantic("GEN", include=semantic)
            if field:
                return field
        raise RuntimeError(f"Could not resolve generator field from {candidates}.")

    def field_map(self) -> dict[str, str]:
        return {
            "bus": self._required(["BusNum"], ["bus", "number"]),
            "id": self._required(["GenID", "ID"], ["id"]),
            "mw": self._required(["GenMW"], ["gen", "mw"]),
            "min": self._required(["GenMWMin", "GenMinMW", "MinMW"], ["min", "mw"]),
            "max": self._required(["GenMWMax", "GenMaxMW", "MaxMW"], ["max", "mw"]),
            "status": (
                self.catalog.choose("GEN", ["GenStatus", "Status"])
                or self.catalog.find_semantic("GEN", include=["status"])
            ),
        }

    def rows(self) -> list[GeneratorControl]:
        f = self.field_map()
        fields = [f["bus"], f["id"], f["mw"], f["min"], f["max"]]
        if f["status"]:
            fields.append(f["status"])

        controls = []
        for row in self.adapter.get_rows("GEN", fields):
            try:
                status = str(row.get(f["status"])) if f["status"] else None
                if status and status.lower().startswith("open"):
                    continue
                controls.append(GeneratorControl(
                    bus=int(row[f["bus"]]),
                    gen_id=str(row[f["id"]]),
                    mw=float(row[f["mw"]]),
                    min_mw=float(row[f["min"]]),
                    max_mw=float(row[f["max"]]),
                    status=status,
                ))
            except (TypeError, ValueError):
                continue
        return controls

    def read_mw(self, bus: int, gen_id: str) -> float | None:
        f = self.field_map()
        rows = self.adapter.get_rows("GEN", [f["bus"], f["id"], f["mw"]])
        for row in rows:
            if int(row[f["bus"]]) == int(bus) and str(row[f["id"]]) == str(gen_id):
                try:
                    return float(row[f["mw"]])
                except (TypeError, ValueError):
                    return None
        return None

    def set_mw(self, control: GeneratorControl, new_mw: float) -> None:
        f = self.field_map()
        self.adapter.change_single(
            "GEN",
            [f["bus"], f["id"], f["mw"]],
            [control.bus, control.gen_id, float(new_mw)],
        )
