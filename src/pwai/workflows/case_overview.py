from __future__ import annotations
from typing import Any
from ..field_catalog import FieldCatalog


class CaseOverview:
    def __init__(self, adapter) -> None:
        self.adapter = adapter
        self.catalog = FieldCatalog(adapter)

    def _count(self, object_type: str) -> int:
        fields = self.catalog.fields(object_type)
        key = next((f.variable for f in fields if "1" in f.key_marker), None)
        if not key:
            key = fields[0].variable if fields else None
        if not key:
            return 0
        return len(self.adapter.get_rows(object_type, [key]))

    def run(self) -> dict[str, Any]:
        return {
            "buses": self._count("BUS"),
            "branches": self._count("BRANCH"),
            "generators": self._count("GEN"),
            "loads": self._count("LOAD"),
        }
