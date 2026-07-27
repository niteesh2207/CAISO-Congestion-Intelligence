from __future__ import annotations
from dataclasses import dataclass


@dataclass(frozen=True)
class FieldInfo:
    key_marker: str
    variable: str
    data_type: str
    description: str


class FieldCatalog:
    def __init__(self, adapter) -> None:
        self.adapter = adapter
        self._cache: dict[str, list[FieldInfo]] = {}

    def fields(self, object_type: str) -> list[FieldInfo]:
        obj = object_type.upper()
        if obj not in self._cache:
            rows = self.adapter.get_field_list(obj)
            self._cache[obj] = [
                FieldInfo(
                    key_marker=str(row.get("key_marker", "")),
                    variable=str(row.get("variable", "")),
                    data_type=str(row.get("data_type", "")),
                    description=str(row.get("description", "")),
                )
                for row in rows
            ]
        return self._cache[obj]

    def has(self, object_type: str, variable: str) -> bool:
        return any(f.variable.lower() == variable.lower() for f in self.fields(object_type))

    def choose(self, object_type: str, candidates: list[str]) -> str | None:
        mapping = {f.variable.lower(): f.variable for f in self.fields(object_type)}
        for candidate in candidates:
            if candidate.lower() in mapping:
                return mapping[candidate.lower()]
        return None

    def find_semantic(
        self,
        object_type: str,
        *,
        include: list[str],
        exclude: list[str] | None = None,
    ) -> str | None:
        exclude = exclude or []
        for field in self.fields(object_type):
            hay = f"{field.variable} {field.description}".lower()
            if all(term.lower() in hay for term in include) and not any(
                term.lower() in hay for term in exclude
            ):
                return field.variable
        return None
