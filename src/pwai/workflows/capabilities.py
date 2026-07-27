from __future__ import annotations

from dataclasses import dataclass
from typing import Any
import re


@dataclass(frozen=True)
class AddonCapability:
    name: str
    available: bool
    evidence: str


class CapabilityRegistry:
    """
    Reads SimAuto ProgramInformation instead of guessing installed licenses.

    ProgramInformation returns:
      version    -> version number / patch information / version string
      addons     -> available add-ons followed by expiration information
      executable -> Simulator executable path
    """

    def __init__(self, adapter) -> None:
        self.adapter = adapter

    @staticmethod
    def _flatten(value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, (list, tuple)):
            out: list[str] = []
            for item in value:
                out.extend(CapabilityRegistry._flatten(item))
            return out
        return [str(value)]

    def snapshot(self) -> dict[str, Any]:
        info = self.adapter.program_information()
        version_tokens = self._flatten(info.get("version"))
        addon_tokens = self._flatten(info.get("addons"))
        executable_tokens = self._flatten(info.get("executable"))

        normalized = " | ".join(addon_tokens).lower()

        def has(patterns: list[str]) -> bool:
            return any(re.search(p, normalized, re.IGNORECASE) for p in patterns)

        opf = has([r"\boptimal power flow\b", r"\bopf\b"])
        scopf = has([r"\bsecurity[- ]?constrained opf\b", r"\bscopf\b"])
        simauto = has([r"\bsimauto\b", r"\bautomation server\b"])
        atc = has([r"\bavailable transfer capability\b", r"\batc\b"])
        pvqv = has([r"\bpvqv\b", r"\bpv.?qv\b", r"\bpv curve\b", r"\bqv curve\b"])
        transient = has([r"\btransient stability\b", r"\btransient\b"])
        reserves = has([r"\bopf reserves\b", r"\breserves\b"])
        itp = has([
            r"\bintegrated topology processing\b",
            r"\btopology processing\b",
        ])
        try:
            major = int(next((x for x in version_tokens if str(x).isdigit()), "0"))
        except ValueError:
            major = 0

        # SCOPF operationally requires OPF as well.
        return {
            "version": version_tokens,
            "addons_raw": addon_tokens,
            "executable": executable_tokens,
            "capabilities": {
                "OPF": {
                    "available": opf,
                    "evidence": "ProgramInformation addons",
                },
                "SCOPF": {
                    "available": scopf and opf,
                    "raw_scopf_present": scopf,
                    "opf_dependency_present": opf,
                    "evidence": "ProgramInformation addons",
                },
                "SIMAUTO": {
                    "available": simauto or self.adapter.solver_backed,
                    "evidence": "active automation adapter + ProgramInformation",
                },
                "ATC": {
                    "available": atc,
                    "evidence": "ProgramInformation addons",
                },
                "PVQV": {
                    "available": pvqv,
                    "evidence": "ProgramInformation addons",
                },
                "TRANSIENT_STABILITY": {
                    "available": transient,
                    "evidence": "ProgramInformation addons",
                },
                "OPF_RESERVES": {
                    "available": reserves and opf,
                    "raw_reserves_present": reserves,
                    "opf_dependency_present": opf,
                    "evidence": "ProgramInformation addons",
                },
                "INTEGRATED_TOPOLOGY_PROCESSING": {
                    "available": itp,
                    "evidence": "ProgramInformation addons",
                },
                "WEATHER_DEPENDENT_LIMITS": {
                    "available": major >= 23,
                    "evidence": "Simulator major version >= 23; feature still requires case configuration",
                },
                "DIFFERENCE_CASE": {
                    "available": major > 0,
                    "evidence": "Simulator base feature",
                },
                "TIME_STEP_SIMULATION_BASE": {
                    "available": major > 0,
                    "evidence": "Simulator base feature",
                },
            },
        }

    def require(self, capability: str) -> dict[str, Any]:
        snapshot = self.snapshot()
        record = snapshot["capabilities"].get(capability.upper())
        if not record or not record.get("available"):
            raise RuntimeError(
                f"PowerWorld capability {capability.upper()} is not available according to ProgramInformation."
            )
        return snapshot
