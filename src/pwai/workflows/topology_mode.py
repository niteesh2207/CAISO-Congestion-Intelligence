from __future__ import annotations

from typing import Any
import json

from ..field_catalog import FieldCatalog
from ..resource_utils import project_or_package_resource
from .object_resolver import BranchIdentity


class FullTopologyIntelligence:
    """
    Planning-to-EMS/full-topology bridge.

    PowerWorld Integrated Topology Processing (ITP) remains authoritative for
    real full-topology consolidation and breaker switching.
    """

    def __init__(self, adapter) -> None:
        self.adapter = adapter
        self.catalog = FieldCatalog(adapter)

    def _config(self) -> dict[str, Any]:
        p = project_or_package_resource("config", "topology_map.json")
        return json.loads(p.read_text(encoding="utf-8"))

    def capability(self) -> dict[str, Any]:
        info = self.adapter.program_information()
        addons = " ".join(str(x) for x in info.get("addons", [])).lower()
        has_itp = (
            "integrated topology" in addons
            or "topology processing" in addons
        )

        branch_fields = []
        try:
            fields = self.catalog.fields("BRANCH")
            branch_fields = [
                {"variable": f.variable, "description": f.description}
                for f in fields
                if any(
                    term in f"{f.variable} {f.description}".lower()
                    for term in [
                        "device type", "breaker", "disconnect",
                        "open or close", "superbus", "topology",
                    ]
                )
            ][:100]
        except Exception:
            pass

        return {
            "itp_addon_signal": has_itp,
            "branch_topology_fields": branch_fields,
            "open_with_breakers_supported_by_powerworld": True,
            "powerworld_script_template": (
                "OpenWithBreakers(BRANCH,[frombus tobus ckt],"
                "[Breaker,Load Break Disconnect],NO);"
            ),
            "mode": "POWERWORLD" if self.adapter.solver_backed else "DEMO",
        }

    def translate_outage(self, branch: BranchIdentity) -> dict[str, Any]:
        if not self.adapter.solver_backed:
            cfg = self._config()
            key1 = f"{branch.from_bus}-{branch.to_bus}-{branch.circuit}"
            key2 = f"{branch.to_bus}-{branch.from_bus}-{branch.circuit}"
            breakers = (
                cfg["planning_to_breakers"].get(key1)
                or cfg["planning_to_breakers"].get(key2)
                or []
            )
            return {
                "mode": "DEMO_TRANSLATION",
                "planning_action": {
                    "type": "OPEN_BRANCH",
                    "branch": vars(branch),
                },
                "full_topology_actions": [
                    {
                        "type": "OPEN_SWITCHING_DEVICE",
                        "device_type": row.get("type"),
                        "name": row.get("name"),
                        "status": row.get("status"),
                    }
                    for row in breakers
                ],
                "provenance": cfg["provenance"],
                "warning": (
                    "Demo breaker mapping is synthetic. Real breaker selection "
                    "must use PowerWorld OpenWithBreakers / Integrated Topology Processing."
                ),
            }

        command = (
            f"OpenWithBreakers(BRANCH,"
            f"[{branch.from_bus} {branch.to_bus} {branch.circuit}],"
            f"[Breaker,Load Break Disconnect],NO);"
        )
        return {
            "mode": "POWERWORLD_COMMAND_PREVIEW",
            "planning_action": {
                "type": "OPEN_BRANCH",
                "branch": vars(branch),
            },
            "powerworld_command": command,
            "capability": self.capability(),
            "warning": (
                "The command is previewed but not executed automatically. "
                "Breaker selection is delegated to PowerWorld's native algorithm."
            ),
        }
