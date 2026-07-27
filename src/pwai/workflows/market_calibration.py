from __future__ import annotations

from pathlib import Path
from typing import Any
import json

from ..resource_utils import project_or_package_resource


REQUIRED_INPUTS = [
    "network_topology",
    "generator_offers_bids",
    "unit_commitment_availability",
    "transmission_outages",
    "transmission_ratings",
    "load_forecast_actual",
    "renewable_forecast_actual",
    "loss_model",
    "market_rules_constraints",
]


class MarketCalibrationAuditor:
    """
    Explicit gate between model economics and market-calibrated intelligence.

    The product never upgrades a PowerWorld model to MARKET_CALIBRATED merely
    because OPF/SCOPF solved. Every required source must be explicitly marked
    verified in the calibration manifest.
    """

    def __init__(self, manifest_path: str | Path | None = None) -> None:
        if manifest_path is None:
            manifest_path = project_or_package_resource("config", "market_calibration.json")
        self.manifest_path = Path(manifest_path)

    def load(self) -> dict[str, Any]:
        if not self.manifest_path.exists():
            return {
                "schema_version": 1,
                "market": None,
                "study_date": None,
                "status": "MODEL_ECONOMICS",
                "inputs": {},
            }
        return json.loads(self.manifest_path.read_text(encoding="utf-8"))

    def audit(self) -> dict[str, Any]:
        manifest = self.load()
        inputs = manifest.get("inputs", {})
        records = []
        missing = []

        for key in REQUIRED_INPUTS:
            record = dict(inputs.get(key, {}))
            verified = bool(record.get("verified", False))
            if not verified:
                missing.append(key)
            records.append({
                "input": key,
                "verified": verified,
                "source": record.get("source", "UNSPECIFIED"),
                "notes": record.get("notes"),
            })

        fully_calibrated = not missing
        status = "MARKET_CALIBRATED" if fully_calibrated else "MODEL_ECONOMICS"

        return {
            "status": status,
            "market": manifest.get("market"),
            "study_date": manifest.get("study_date"),
            "verified_inputs": sum(1 for r in records if r["verified"]),
            "required_inputs": len(REQUIRED_INPUTS),
            "missing_or_unverified": missing,
            "records": records,
            "guardrail": (
                "PowerWorld model LMPs and SCOPF marginal costs remain MODEL_ECONOMICS until all required "
                "market inputs are explicitly verified. Paid/licensed data must only be used when the user "
                "provides authorized access."
            ),
        }
